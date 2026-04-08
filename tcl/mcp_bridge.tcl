# =============================================================================
# mcp_bridge.tcl — TCP socket server for MCP ↔ SimVision communication
# =============================================================================
# Load this script inside SimVision (Cadence Xcelium waveform viewer):
#   xrun -gui -input "@simvision {source mcp_bridge.tcl}" design.v
#   -- or --
#   In SimVision console: source /path/to/mcp_bridge.tcl
#
# Protocol:
#   Request  → "<command>\n"
#   Response → "OK <len>\n<body>\n<<<END>>>\n"       (success)
#              "ERROR <len>\n<message>\n<<<END>>>\n"  (failure)
#
# Meta commands:
#   __PING__              → "OK 4\npong\n<<<END>>>\n"
#   __SCREENSHOT__ <path> → capture waveform to PostScript file
#   __QUIT__              → close connection
#   __SHUTDOWN__          → safe shutdown: close SHM database + finish
#   __RUN_ASYNC__ <dur>   → non-blocking sim run, returns immediately
#   __PROGRESS__          → current sim time (works during async run)
#   __WATCH__ <sig> <op> <val> → stop when signal matches condition
#   __WATCH_LIST__        → list active watchpoints
#   __WATCH_CLEAR__ <id|all> → delete watchpoint(s)
#   __PROBE_CONTROL__ <enable|disable|status> [scope] → toggle SHM probe recording
#   __SAVE__ <name>       → save simulation checkpoint
#   __RESTORE__ <name>    → restore from checkpoint (empty = last saved)
#   __BISECT__ <sig> <op> <val> <start_ns> <end_ns> [<precision_ns>]
#                         → binary search for bug time (default precision: 1000ns)
# =============================================================================

namespace eval ::mcp_bridge {
    variable server_socket ""
    variable client_channel ""
    variable port 9876
    variable port_range 10
    variable bridge_type "xmsim"
    variable cmd_buffer ""
    variable async_running 0
    variable async_done 0
    variable async_stop_reason ""
    variable watch_ids [list]
    variable _checkpoint_dir ""
    variable _checkpoint_name ""
    variable _init_snapshot_dir ""
    variable _shutdown_flag 0

    # Per-user temp directory (matches Python side: /tmp/xcelium_mcp_{uid}/)
    variable uid [exec id -u]
    variable user_tmp "/tmp/xcelium_mcp_$uid"
    catch {file mkdir $user_tmp}
}

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------
proc ::mcp_bridge::init {} {
    variable port
    variable port_range
    variable bridge_type
    variable server_socket

    # P1-1: Detect bridge type (xmsim vs SimVision)
    if {[info commands waveform] ne ""} {
        set bridge_type "simvision"
    } else {
        set bridge_type "xmsim"
    }
    puts "MCP Bridge: type=$bridge_type"

    # Allow port override via environment variable
    if {[info exists ::env(MCP_BRIDGE_PORT)]} {
        set port $::env(MCP_BRIDGE_PORT)
    }

    # Close existing server if re-sourced
    if {$server_socket ne ""} {
        catch {close $server_socket}
        set server_socket ""
    }

    # P1-2: Auto port — try port_range ports starting from base
    set found 0
    for {set p $port} {$p < $port + $port_range} {incr p} {
        if {![catch {socket -server ::mcp_bridge::accept $p} sock]} {
            set server_socket $sock
            set port $p
            set found 1
            puts "MCP Bridge: listening on port $p"
            break
        }
        puts "MCP Bridge: port $p busy, trying next..."
    }
    if {!$found} {
        puts "MCP Bridge: ERROR — all ports $port-[expr {$port + $port_range - 1}] busy"
        return
    }

    # P1-3: Ready file — "port type timestamp" format
    variable user_tmp
    set ready_file "$user_tmp/bridge_ready_$port"
    if {[catch {
        set f [open $ready_file w]
        puts $f "$port $bridge_type [clock seconds]"
        close $f
    } err]} {
        puts "MCP Bridge: WARNING: could not create ready file: $err"
    }

    # Save init snapshot for sim_restart fallback
    ::mcp_bridge::on_init

    # v4: Source project setup TCL via MCP_SETUP_TCL env var
    # When sim_start sets MCP_SETUP_TCL, this sources the project's original
    # setup.tcl (probe settings, dump scope, etc.) after bridge initialization.
    # IMPORTANT: Intercept 'run', 'exit', 'finish' during source — these are
    # batch commands that would block or terminate the bridge. Only probe/database
    # setup should execute. Commands are restored after source completes.
    if {[info exists ::env(MCP_SETUP_TCL)] && $::env(MCP_SETUP_TCL) ne ""} {
        if {[file exists $::env(MCP_SETUP_TCL)]} {
            # MCP_SETUP_TCL is pre-filtered by sim_start (Python side)
            # to remove run/exit/finish/database-close lines.
            # Only probe/database-open setup remains.
            puts "MCP Bridge: sourcing setup TCL: $::env(MCP_SETUP_TCL)"
            source $::env(MCP_SETUP_TCL)
            puts "MCP Bridge: setup TCL loaded"
        } else {
            puts "MCP Bridge: WARNING — MCP_SETUP_TCL not found: $::env(MCP_SETUP_TCL)"
        }
    }
}

# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------
proc ::mcp_bridge::accept {channel addr port} {
    variable client_channel

    # Only allow one client at a time
    if {$client_channel ne ""} {
        puts $channel "ERROR 30\nAnother client is connected\n<<<END>>>"
        close $channel
        return
    }

    set client_channel $channel
    fconfigure $channel -buffering line -translation lf -encoding utf-8
    fileevent $channel readable [list ::mcp_bridge::on_readable $channel]
    puts "MCP Bridge: client connected from $addr:$port"
}

proc ::mcp_bridge::on_readable {channel} {
    variable client_channel
    variable cmd_buffer

    if {[eof $channel]} {
        ::mcp_bridge::disconnect $channel
        return
    }

    if {[gets $channel line] < 0} {
        return
    }

    # Accumulate into buffer (support multi-line commands ending with <<<EXEC>>>)
    if {$line eq "<<<EXEC>>>"} {
        set cmd $cmd_buffer
        set cmd_buffer ""
        ::mcp_bridge::dispatch $channel $cmd
    } elseif {$cmd_buffer eq ""} {
        # Single-line command (no <<<EXEC>>> needed for simple commands)
        ::mcp_bridge::dispatch $channel $line
    } else {
        append cmd_buffer $line "\n"
    }
}

proc ::mcp_bridge::disconnect {channel} {
    variable client_channel
    catch {close $channel}
    if {$client_channel eq $channel} {
        set client_channel ""
    }
    puts "MCP Bridge: client disconnected"
}

# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------
proc ::mcp_bridge::dispatch {channel cmd} {
    set cmd [string trim $cmd]
    if {$cmd eq ""} return

    # Meta commands
    if {$cmd eq "__PING__"} {
        ::mcp_bridge::send_ok $channel "pong"
        return
    }

    if {$cmd eq "__QUIT__"} {
        ::mcp_bridge::send_ok $channel "bye"
        ::mcp_bridge::disconnect $channel
        return
    }

    if {[string match "__SCREENSHOT__*" $cmd]} {
        set path [string trim [string range $cmd 16 end]]
        ::mcp_bridge::do_screenshot $channel $path
        return
    }

    # --- Phase 1 meta commands ---

    if {$cmd eq "__SHUTDOWN__"} {
        ::mcp_bridge::do_shutdown $channel
        return
    }

    if {$cmd eq "__RESTART__"} {
        ::mcp_bridge::do_restart $channel
        return
    }

    if {[string match "__EXECUTE_TCL__*" $cmd]} {
        set tcl_cmd [string trim [string range $cmd 16 end]]
        ::mcp_bridge::do_execute_tcl $channel $tcl_cmd
        return
    }

    if {[string match "__RUN_ASYNC__*" $cmd]} {
        set duration [string trim [string range $cmd 14 end]]
        ::mcp_bridge::do_run_async $channel $duration
        return
    }

    if {$cmd eq "__PROGRESS__"} {
        ::mcp_bridge::do_progress $channel
        return
    }

    if {[string match "__WATCH__*" $cmd]} {
        set args [string trim [string range $cmd 9 end]]
        ::mcp_bridge::do_watch $channel $args
        return
    }

    if {$cmd eq "__WATCH_LIST__"} {
        ::mcp_bridge::do_watch_list $channel
        return
    }

    if {[string match "__WATCH_CLEAR__*" $cmd]} {
        set id [string trim [string range $cmd 15 end]]
        ::mcp_bridge::do_watch_clear $channel $id
        return
    }

    # --- Phase 2 meta commands ---

    if {[string match "__PROBE_CONTROL__*" $cmd]} {
        set mode [string trim [string range $cmd 17 end]]
        ::mcp_bridge::do_probe_control $channel $mode
        return
    }

    if {[string match "__SAVE__*" $cmd]} {
        # Protocol: "__SAVE__ {name} {dir}"  — dir is optional, defaults to $user_tmp/checkpoints
        set args [string trim [string range $cmd 8 end]]
        set parts [split $args " "]
        set name [lindex $parts 0]
        set dir  [lindex $parts 1]
        ::mcp_bridge::do_save $channel $name $dir
        return
    }

    if {[string match "__RESTORE__*" $cmd]} {
        # Protocol: "__RESTORE__ {name} {dir}"  — dir is optional
        set args [string trim [string range $cmd 11 end]]
        set parts [split $args " "]
        set name [lindex $parts 0]
        set dir  [lindex $parts 1]
        ::mcp_bridge::do_restore $channel $name $dir
        return
    }

    # --- Round-trip optimization meta commands ---

    if {$cmd eq "__STATUS__"} {
        ::mcp_bridge::do_status $channel
        return
    }

    if {[string match "__RUN_AND_REPORT__*" $cmd]} {
        # __RUN_AND_REPORT__ = 18 chars
        set duration [string trim [string range $cmd 18 end]]
        ::mcp_bridge::do_run_and_report $channel $duration
        return
    }

    if {[string match "__DEPOSIT_AND_VERIFY__*" $cmd]} {
        # __DEPOSIT_AND_VERIFY__ = 22 chars
        set args [string trim [string range $cmd 22 end]]
        set parts [split $args " "]
        set signal [lindex $parts 0]
        set val [lindex $parts 1]
        ::mcp_bridge::do_deposit_and_verify $channel $signal $val
        return
    }

    if {[string match "__RELEASE_AND_VERIFY__*" $cmd]} {
        # __RELEASE_AND_VERIFY__ = 22 chars
        set signal [string trim [string range $cmd 22 end]]
        ::mcp_bridge::do_release_and_verify $channel $signal
        return
    }

    if {$cmd eq "__DEBUG_SNAPSHOT__"} {
        ::mcp_bridge::do_debug_snapshot $channel
        return
    }

    # --- Phase 5 meta commands ---

    if {[string match "__WAVEFORM_ADD__*" $cmd]} {
        # Protocol: "__WAVEFORM_ADD__ {group_name_or_""} sig1 sig2 ..."
        # __WAVEFORM_ADD__ = 16 chars
        # Use Tcl list parsing (lindex/lrange) to handle {braced group names}
        set args [string trim [string range $cmd 16 end]]
        set group_name [lindex $args 0]
        # Normalize: "" placeholder → empty string (no group)
        if {$group_name eq {""}} { set group_name "" }
        set sig_list [lrange $args 1 end]
        ::mcp_bridge::do_waveform_add $channel $group_name $sig_list
        return
    }

    if {[string match "__WAVEFORM_REMOVE__*" $cmd]} {
        # Protocol: "__WAVEFORM_REMOVE__ {group_name_or_""} sig1 sig2 ..."
        # Mirrors __WAVEFORM_ADD__ protocol.
        # __WAVEFORM_REMOVE__ = 19 chars
        set args [string trim [string range $cmd 19 end]]
        set group_name [lindex $args 0]
        if {$group_name eq {""}} { set group_name "" }
        set sig_list [lrange $args 1 end]
        ::mcp_bridge::do_waveform_remove $channel $group_name $sig_list
        return
    }

    # --- Phase 3 meta commands ---

    if {[string match "__BISECT__*" $cmd]} {
        set args [string trim [string range $cmd 10 end]]
        ::mcp_bridge::do_bisect $channel $args
        return
    }

    # Regular Tcl/SimVision command — evaluate in global namespace
    if {[catch {uplevel #0 $cmd} result]} {
        ::mcp_bridge::send_error $channel $result
    } else {
        ::mcp_bridge::send_ok $channel $result
    }
}

# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
proc ::mcp_bridge::send_ok {channel body} {
    set len [string length $body]
    puts $channel "OK $len"
    puts $channel $body
    puts $channel "<<<END>>>"
    flush $channel
}

proc ::mcp_bridge::send_error {channel body} {
    set len [string length $body]
    puts $channel "ERROR $len"
    puts $channel $body
    puts $channel "<<<END>>>"
    flush $channel
}

# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_screenshot {channel path} {
    if {$path eq ""} {
        set path "$::mcp_bridge::user_tmp/screenshot_[clock seconds].ps"
    }

    # Try SimVision waveform print first
    # -page_height_number 1 prevents "bad value 0" error when groups are collapsed
    if {[catch {
        waveform print -file $path -page_height_number 1
    } err1]} {
        # Fallback: try X11 window capture via 'import' (ImageMagick)
        if {[catch {
            set wid [winfo id .]
            exec import -window $wid $path
        } err2]} {
            ::mcp_bridge::send_error $channel \
                "Screenshot failed: waveform print=$err1, import=$err2"
            return
        }
    }

    ::mcp_bridge::send_ok $channel $path
}

# ---------------------------------------------------------------------------
# F0: __RESTART__ — safe restart with run-clean → snapshot → plain fallback
# ---------------------------------------------------------------------------
proc ::mcp_bridge::init_snapshot {} {
    variable _init_snapshot_dir
    variable user_tmp
    set _init_snapshot_dir "$user_tmp/init_snapshot"
    file mkdir $_init_snapshot_dir
    catch {save -simulation mcp_init -path $_init_snapshot_dir -overwrite}
}

proc ::mcp_bridge::on_init {} {
    variable _init_snapshot_dir
    variable user_tmp
    set _init_snapshot_dir "$user_tmp/init_snapshot"
    if {[file exists $_init_snapshot_dir]} {
        catch {file delete -force $_init_snapshot_dir}
    }
    ::mcp_bridge::init_snapshot
}

proc ::mcp_bridge::do_restart {channel} {
    variable _init_snapshot_dir

    # Method 1: run -clean (full restart, cleanest)
    set err_a ""
    if {![catch {run -clean} err_a]} {
        ::mcp_bridge::send_ok $channel "restarted:run-clean|time:0"
        return
    }

    # Method 2: restore init snapshot (saved at bridge startup)
    set err_b "(no init snapshot)"
    if {[info exists _init_snapshot_dir] && $_init_snapshot_dir ne "" \
            && [file exists $_init_snapshot_dir]} {
        if {![catch {restart worklib.mcp_init:module -path $_init_snapshot_dir} err_b]} {
            catch {stop -delete -all}
            ::mcp_bridge::send_ok $channel "restarted:snapshot|time:0"
            return
        }
    }

    # Method 3: plain restart (SimVision built-in)
    set err_c ""
    if {![catch {restart} err_c]} {
        ::mcp_bridge::send_ok $channel "restarted:plain|time:0"
        return
    }

    ::mcp_bridge::send_error $channel \
        "restart failed: run-clean='$err_a' snapshot='$err_b' plain='$err_c'"
}

# ---------------------------------------------------------------------------
# F0b: __EXECUTE_TCL__ — execute arbitrary Tcl in global namespace
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_execute_tcl {channel cmd_str} {
    if {[catch {uplevel #0 $cmd_str} result]} {
        ::mcp_bridge::send_error $channel "TclError: $result"
        return
    }
    ::mcp_bridge::send_ok $channel $result
}

# ---------------------------------------------------------------------------
# F1: __SHUTDOWN__ — safe shutdown (database close + finish)
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_shutdown {channel} {
    variable bridge_type
    variable port
    variable _shutdown_flag

    # 1. Close all SHM databases to flush data
    if {$bridge_type eq "simvision"} {
        # SimVision uses "database close" (not "database -close")
        catch {
            set dbs [database find]
            foreach db $dbs {
                catch {database close $db}
            }
        }
    } else {
        if {[catch {
            set dbs [database -list]
            foreach db $dbs {
                catch {database -close $db}
            }
        } err]} {
            catch {database -close ../dump/ci_top.shm}
        }
    }

    # 2. Cleanup ready file
    catch {file delete "$::mcp_bridge::user_tmp/bridge_ready_$port"}

    # 3. Notify client before termination
    ::mcp_bridge::send_ok $channel "shutdown:ok"

    # 4. Set shutdown flag (unblocks vwait/event pump) + schedule exit
    set _shutdown_flag 1
    if {$bridge_type eq "simvision"} {
        after 100 {exit}
    } else {
        after 100 {finish}
    }
}

# ---------------------------------------------------------------------------
# F2: __RUN_ASYNC__ — non-blocking simulation run
#     __PROGRESS__ — query current sim time during async run
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_run_async {channel duration} {
    variable async_running
    variable async_done
    variable async_stop_reason

    if {$async_running} {
        ::mcp_bridge::send_error $channel "async run already in progress"
        return
    }

    set async_running 1
    set async_done 0
    set async_stop_reason ""

    set run_cmd "run"
    if {$duration ne ""} {
        set run_cmd "run $duration"
    }

    after idle [list ::mcp_bridge::_async_run_exec $run_cmd]
    ::mcp_bridge::send_ok $channel "async_started:$duration"
}

proc ::mcp_bridge::_async_run_exec {run_cmd} {
    variable async_running
    variable async_done
    variable async_stop_reason

    if {[catch {uplevel #0 $run_cmd} result]} {
        set async_stop_reason "error:$result"
    } else {
        if {[catch {set w [where]} err]} {
            set async_stop_reason "completed"
        } else {
            set async_stop_reason "stopped:$w"
        }
    }

    set async_running 0
    set async_done 1
}

proc ::mcp_bridge::do_progress {channel} {
    variable async_running
    variable async_done
    variable async_stop_reason

    if {[catch {set t [where]} err]} {
        set t "unknown"
    }

    set status "idle"
    if {$async_running} {
        set status "running"
    } elseif {$async_done} {
        set status "done"
    }

    ::mcp_bridge::send_ok $channel "time:$t|status:$status|reason:$async_stop_reason"
}

# ---------------------------------------------------------------------------
# F3: __WATCH__ — signal watchpoint (conditional stop)
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_watch {channel args_str} {
    variable watch_ids

    set parts [split $args_str]
    if {[llength $parts] < 3} {
        ::mcp_bridge::send_error $channel \
            "Usage: __WATCH__ signal op value (e.g. __WATCH__ top.dut.state == 3)"
        return
    }

    set signal [lindex $parts 0]
    set op [lindex $parts 1]
    set value [lindex $parts 2]

    set condition "\{\[value $signal\] $op \"$value\"\}"

    if {[catch {
        set stop_id [eval stop -create -condition $condition -silent]
    } err]} {
        ::mcp_bridge::send_error $channel "watch failed: $err"
        return
    }

    lappend watch_ids $stop_id
    ::mcp_bridge::send_ok $channel "watch:$stop_id|signal:$signal|condition:$op $value"
}

proc ::mcp_bridge::do_watch_list {channel} {
    variable watch_ids

    if {[llength $watch_ids] == 0} {
        ::mcp_bridge::send_ok $channel "no active watchpoints"
        return
    }

    set result ""
    foreach id $watch_ids {
        if {[catch {set info [stop -show $id]} err]} {
            append result "$id: (removed or invalid)\n"
        } else {
            append result "$id: $info\n"
        }
    }

    ::mcp_bridge::send_ok $channel [string trimright $result "\n"]
}

proc ::mcp_bridge::do_watch_clear {channel id} {
    variable watch_ids

    if {$id eq "all"} {
        foreach wid $watch_ids {
            catch {stop -delete $wid}
        }
        set watch_ids [list]
        ::mcp_bridge::send_ok $channel "all watchpoints cleared"
        return
    }

    if {[catch {stop -delete $id} err]} {
        ::mcp_bridge::send_error $channel "watch_clear failed: $err"
        return
    }

    set idx [lsearch -exact $watch_ids $id]
    if {$idx >= 0} {
        set watch_ids [lreplace $watch_ids $idx $idx]
    }

    ::mcp_bridge::send_ok $channel "watchpoint $id cleared"
}

# ---------------------------------------------------------------------------
# F4: __PROBE_CONTROL__ — selective probe enable/disable for SHM size control
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_probe_control {channel args_str} {
    # Parse: mode [scope]
    # Examples:
    #   __PROBE_CONTROL__ disable              → disable all probes
    #   __PROBE_CONTROL__ enable top.hw.u_ext  → enable probes in scope only
    #   __PROBE_CONTROL__ status
    set parts [split $args_str]
    set mode [lindex $parts 0]
    set scope [expr {[llength $parts] >= 2 ? [lindex $parts 1] : "*"}]

    switch -exact $mode {
        "disable" {
            if {[catch {probe -disable $scope} err]} {
                ::mcp_bridge::send_error $channel "probe disable failed: $err"
                return
            }
            ::mcp_bridge::send_ok $channel "probe:disabled|scope:$scope"
        }
        "enable" {
            if {[catch {probe -enable $scope} err]} {
                ::mcp_bridge::send_error $channel "probe enable failed: $err"
                return
            }
            ::mcp_bridge::send_ok $channel "probe:enabled|scope:$scope"
        }
        "status" {
            if {[catch {set info [database -list]} err]} {
                set info "unknown"
            }
            ::mcp_bridge::send_ok $channel "probe_databases:$info"
        }
        default {
            ::mcp_bridge::send_error $channel \
                "Usage: __PROBE_CONTROL__ enable|disable|status [scope]"
        }
    }
}

# ---------------------------------------------------------------------------
# F5: __SAVE__ / __RESTORE__ — simulation checkpoint
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_save {channel name {dir ""}} {
    variable _checkpoint_dir
    variable _checkpoint_name

    if {$name eq ""} {
        set name "chk_[clock seconds]"
    }
    if {$dir eq ""} {
        set dir "$::mcp_bridge::user_tmp/checkpoints"
    }

    # 1. Ensure simulator is stopped before save
    if {[catch {set st [status]} err]} { set st "" }
    if {![string match "*stopped*" $st]} { catch {stop} }

    # 2. Create checkpoint directory
    file mkdir $dir

    # 3. Execute save — use worklib.NAME:module format (consistent with do_restore)
    set snapshot "worklib.$name:module"
    if {[catch {save -simulation $snapshot -path $dir -overwrite} err]} {
        # Fallback: some xmsim versions don't support -path for save
        if {[catch {save -simulation $snapshot -overwrite} err2]} {
            ::mcp_bridge::send_error $channel "save failed: $err2"
            return
        }
    }

    set _checkpoint_dir $dir
    set _checkpoint_name $name

    ::mcp_bridge::send_ok $channel "saved:worklib.$name:module|dir:$dir"
}

proc ::mcp_bridge::do_restore {channel name {dir ""}} {
    variable _checkpoint_dir
    variable _checkpoint_name

    if {$name eq ""} {
        if {![info exists _checkpoint_name] || $_checkpoint_name eq ""} {
            ::mcp_bridge::send_error $channel "no checkpoint name given and no previous save"
            return
        }
        set name $_checkpoint_name
    }
    if {$dir eq ""} {
        if {[info exists _checkpoint_dir] && $_checkpoint_dir ne ""} {
            set dir $_checkpoint_dir
        } else {
            set dir "$::mcp_bridge::user_tmp/checkpoints"
        }
    }

    set snapshot "worklib.$name:module"

    # 1. Restore simulation state
    if {[catch {restart $snapshot -path $dir} err]} {
        ::mcp_bridge::send_error $channel "restore failed: $err"
        return
    }

    # 2. Clear stale breakpoints to prevent spurious $finish (P4-9)
    catch {stop -delete -all}

    set _checkpoint_dir $dir
    set _checkpoint_name $name

    if {[catch {set w [where]} err]} {
        set w "unknown"
    }

    ::mcp_bridge::send_ok $channel "restored:$snapshot|position:$w"
}

# ---------------------------------------------------------------------------
# F6: __BISECT__ — automated binary search for bug time
# ---------------------------------------------------------------------------
proc ::mcp_bridge::_get_sim_time_ns {} {
    # Parse current simulation time into nanoseconds.
    # Uses 'status' command which always shows "Simulation Time - X MS + Y"
    # even when stopped at a breakpoint (where 'where' shows file/line instead).
    set txt ""
    catch {set txt [where]}
    catch {append txt " " [status]}

    # Match "X MS + Y" anywhere in combined output
    if {[regexp {(\d+)\s+MS\s*\+\s*(\d+)} $txt -> ms sub]} {
        return [expr {$ms * 1000000 + $sub}]
    }
    # Match "X NS + Y"
    if {[regexp {(\d+)\s+NS\s*\+\s*(\d+)} $txt -> ns sub]} {
        return [expr {$ns + $sub}]
    }
    # Match standalone "X NS"
    if {[regexp {(\d+)\s+NS} $txt -> ns]} {
        return $ns
    }
    return 0
}

proc ::mcp_bridge::do_bisect {channel args_str} {
    # Parse: __BISECT__ signal op value start_ns end_ns [precision_ns]
    set parts [split $args_str]
    if {[llength $parts] < 5} {
        ::mcp_bridge::send_error $channel \
            "Usage: __BISECT__ signal op value start_ns end_ns [precision_ns]"
        return
    }

    set signal    [lindex $parts 0]
    set op        [lindex $parts 1]
    set value     [lindex $parts 2]
    set start_ns  [lindex $parts 3]
    set end_ns    [lindex $parts 4]
    set precision [expr {[llength $parts] >= 6 ? [lindex $parts 5] : 1000}]
    set max_iter 20
    set iteration 0
    set log_lines [list]

    lappend log_lines "bisect_start|range:${start_ns}-${end_ns}ns|precision:${precision}ns"

    set chk_dir "$::mcp_bridge::user_tmp/bisect"
    file mkdir $chk_dir

    # Save checkpoint at time 0
    set t0_name "bisect_t0"
    if {[catch {save -simulation $t0_name -path $chk_dir -overwrite} err]} {
        ::mcp_bridge::send_error $channel "bisect: save t0 failed: $err"
        return
    }
    set t0_snapshot "worklib.$t0_name:module"

    # Create start checkpoint: restore t0 → run to start_ns → save
    set start_name "bisect_start"
    set start_snapshot "worklib.$start_name:module"
    set cached_start_ns $start_ns

    if {$start_ns > 0} {
        catch {restart $t0_snapshot -path $chk_dir}
        catch {run ${start_ns}ns}
    }
    if {[catch {save -simulation $start_name -path $chk_dir -overwrite} err]} {
        ::mcp_bridge::send_error $channel "bisect: save start failed: $err"
        return
    }
    lappend log_lines "checkpoint|t0:saved|start:${start_ns}ns:saved"

    while {($end_ns - $start_ns) > $precision && $iteration < $max_iter} {
        incr iteration
        set mid_ns [expr {($start_ns + $end_ns) / 2}]

        # Restore to start checkpoint (skip re-running known-good region)
        if {[catch {restart $start_snapshot -path $chk_dir} err]} {
            ::mcp_bridge::send_error $channel "bisect: restore failed iter $iteration: $err"
            return
        }

        # Set watchpoint at start_ns
        set condition "\{\[value $signal\] $op \"$value\"\}"
        if {[catch {set stop_id [eval stop -create -condition $condition -silent]} err]} {
            ::mcp_bridge::send_error $channel "bisect: watch failed iter $iteration: $err"
            return
        }

        # Run from start_ns toward mid_ns
        set run_dur [expr {$mid_ns - $start_ns}]
        catch {run ${run_dur}ns}

        set cur_ns [::mcp_bridge::_get_sim_time_ns]

        # Verify: stopped before mid AND signal matches
        set hit 0
        if {$cur_ns < $mid_ns} {
            if {[catch {set v [value $signal]} err]} { set v "?" }
            if {$v eq $value} {
                set hit 1
            }
        }

        catch {stop -delete $stop_id}

        if {$hit} {
            # Bug in [start, cur_ns] — narrow end
            set end_ns $cur_ns
            lappend log_lines "iter:$iteration|mid:${mid_ns}|HIT:${cur_ns}ns|range:${start_ns}-${end_ns}"
        } else {
            # Bug in [mid, end] — advance start, update start checkpoint
            set start_ns $mid_ns

            # Rebuild start checkpoint at new start_ns
            catch {restart $t0_snapshot -path $chk_dir}
            if {$start_ns > 0} {
                catch {run ${start_ns}ns}
            }
            catch {save -simulation $start_name -path $chk_dir -overwrite}
            set cached_start_ns $start_ns

            lappend log_lines "iter:$iteration|mid:${mid_ns}|miss:${cur_ns}ns|range:${start_ns}-${end_ns}|start_chk:updated"
        }
    }

    # Final: restore and run to the bug time for inspection
    catch {restart $t0_snapshot -path $chk_dir}
    if {$end_ns > 0} {
        catch {run ${end_ns}ns}
    }
    if {[catch {set final_val [value $signal]} err]} { set final_val "?" }

    lappend log_lines "bisect_done|iters:$iteration|found:${start_ns}-${end_ns}ns|value:$final_val"
    ::mcp_bridge::send_ok $channel [join $log_lines "\n"]
}

# ---------------------------------------------------------------------------
# F8: Round-trip optimization meta commands
# ---------------------------------------------------------------------------

# __STATUS__ — where + scope in 1 call (was 2 round-trips)
proc ::mcp_bridge::do_status {channel} {
    set pos "(unknown)"
    set sc "(unknown)"
    catch {set pos [where]}
    catch {set sc [scope]}
    ::mcp_bridge::send_ok $channel "Position: $pos\nScope: $sc"
}

# __RUN_AND_REPORT__ — run + where in 1 call (was 2 round-trips)
proc ::mcp_bridge::do_run_and_report {channel duration} {
    if {$duration ne ""} {
        if {[catch {run $duration} err]} {
            set pos "(run failed: $err)"
            catch {set pos [where]}
            ::mcp_bridge::send_ok $channel "RUN_ERROR:$err\n$pos"
            return
        }
    } else {
        if {[catch {run} err]} {
            set pos "(run failed: $err)"
            catch {set pos [where]}
            ::mcp_bridge::send_ok $channel "RUN_ERROR:$err\n$pos"
            return
        }
    }
    set pos "(unknown)"
    catch {set pos [where]}
    ::mcp_bridge::send_ok $channel $pos
}

# __DEPOSIT_AND_VERIFY__ — deposit + value readback in 1 call (was 2 round-trips)
proc ::mcp_bridge::do_deposit_and_verify {channel signal val} {
    deposit $signal $val
    set readback "(unknown)"
    catch {set readback [value $signal]}
    ::mcp_bridge::send_ok $channel $readback
}

# __RELEASE_AND_VERIFY__ — release + value readback in 1 call (was 2 round-trips)
proc ::mcp_bridge::do_release_and_verify {channel signal} {
    release $signal
    set readback "(unknown)"
    catch {set readback [value $signal]}
    ::mcp_bridge::send_ok $channel $readback
}

# __DEBUG_SNAPSHOT__ — where + scope + stop -show in 1 call (was 3+ round-trips)
proc ::mcp_bridge::do_debug_snapshot {channel} {
    set pos "(unknown)"
    set sc "(unknown)"
    set stops "(none)"
    catch {set pos [where]}
    catch {set sc [scope]}
    catch {set stops [stop -show]}
    ::mcp_bridge::send_ok $channel "POSITION:$pos\nSCOPE:$sc\nSTOPS:$stops"
}

# ---------------------------------------------------------------------------
# F7: __WAVEFORM_ADD__ — Add signals/groups to waveform (P5-2)
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_waveform_add {channel group_name sig_list} {
    # 0. Ensure a waveform window exists (auto-create if none)
    if {[catch {waveform using}]} {
        waveform new
    }

    # 1. Resolve DB prefix (SimVision requires db_name::signal_path)
    set db_prefix ""
    if {![catch {set db_name [database find]}]} {
        set db_name [string trim $db_name]
        if {[llength $db_name] > 1} {
            set db_name [lindex $db_name 0]
        }
        if {$db_name ne ""} {
            set db_prefix "${db_name}::"
        }
    }

    # 2. Collect existing signals for duplicate detection
    set existing {}
    catch {set existing [waveform signals -format fullpath]}

    # 3. Filter: resolve DB prefix + skip duplicates
    set to_add {}
    foreach sig $sig_list {
        # Add db_prefix if signal doesn't already have ::
        if {$db_prefix ne "" && [string first "::" $sig] < 0} {
            set full "${db_prefix}${sig}"
        } else {
            set full $sig
        }
        if {[lsearch -exact $existing $full] < 0 && [lsearch -exact $existing $sig] < 0} {
            lappend to_add $full
        }
    }

    set skipped [expr {[llength $sig_list] - [llength $to_add]}]

    if {[llength $to_add] == 0} {
        set label [expr {$group_name ne "" ? $group_name : "default"}]
        ::mcp_bridge::send_ok $channel \
            "added:0|skipped:$skipped|group:$label (all signals already present)"
        return
    }

    # 4. Add signals — use SimVision group if group_name given
    if {$group_name ne ""} {
        # Create a named group containing the signals, then add group to waveform.
        # group new -contents expects a Tcl list of signal paths.
        if {[catch {group new -name $group_name -contents $to_add} err]} {
            ::mcp_bridge::send_error $channel "group new failed: $err"
            return
        }
        # waveform add -groups requires double-bracing for names with spaces
        if {[catch {waveform add -groups [list $group_name]} err]} {
            ::mcp_bridge::send_error $channel "waveform add -groups failed: $err"
            return
        }
    } else {
        if {[catch {waveform add -signals $to_add} err]} {
            ::mcp_bridge::send_error $channel "waveform add failed: $err"
            return
        }
    }

    set label [expr {$group_name ne "" ? $group_name : "default"}]
    ::mcp_bridge::send_ok $channel \
        "added:[llength $to_add]|skipped:$skipped|group:$label"
}

# ---------------------------------------------------------------------------
# Helper: find a waveform group ID by name
# ---------------------------------------------------------------------------
proc ::mcp_bridge::find_group_id {group_name} {
    if {[catch {set grp_ids [waveform find -type group]}]} {
        return ""
    }
    foreach id $grp_ids {
        set name ""
        catch {set name [waveform signals $id]}
        if {$name eq $group_name || $name eq "{$group_name}"} {
            return $id
        }
    }
    return ""
}

# ---------------------------------------------------------------------------
# F8: __WAVEFORM_REMOVE__ — Unified signal/group removal (mirrors ADD)
#
# Protocol: __WAVEFORM_REMOVE__ {group_or_""} sig1 sig2 ...
#   group + signals → remove matching signals within that group only
#   group + no sigs → remove entire group and its child signals
#   "" + signals    → remove matching signals from all groups/ungrouped
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_waveform_remove {channel group_name sig_list} {
    # Case 1: group_name + no signals → remove entire group
    if {$group_name ne "" && [llength $sig_list] == 0} {
        set found_id [::mcp_bridge::find_group_id $group_name]
        if {$found_id eq ""} {
            ::mcp_bridge::send_error $channel "Group '$group_name' not found"
            return
        }
        catch {waveform clear $found_id}
        catch {group delete $group_name}
        ::mcp_bridge::send_ok $channel "removed group: $group_name"
        return
    }

    # Case 2 & 3: remove matching signals (scoped to group or all)
    if {$group_name ne ""} {
        set found_id [::mcp_bridge::find_group_id $group_name]
        if {$found_id eq ""} {
            ::mcp_bridge::send_error $channel "Group '$group_name' not found"
            return
        }
        set search_ids [waveform hierarchy contents $found_id]
        set search_paths {}
        foreach cid $search_ids {
            catch {lappend search_paths [waveform signals -format fullpath $cid]}
        }
    } else {
        if {[catch {set search_ids [waveform find -type signal]}]} {
            ::mcp_bridge::send_ok $channel "removed:0|scope:all (no waveform)"
            return
        }
        set search_paths [waveform signals -format fullpath]
    }

    # Collect IDs to remove first, then batch-delete (avoids stale ID issue)
    set ids_to_remove {}
    set not_found {}

    foreach target $sig_list {
        set found 0
        foreach path $search_paths id $search_ids {
            if {[string match "*${target}*" $path]} {
                lappend ids_to_remove $id
                set found 1
                break
            }
        }
        if {!$found} {
            lappend not_found $target
        }
    }

    set removed 0
    foreach id $ids_to_remove {
        catch {waveform clear $id}
        incr removed
    }

    set scope [expr {$group_name ne "" ? "group:$group_name" : "all"}]
    set msg "removed:$removed|scope:$scope"
    if {[llength $not_found] > 0} {
        append msg "|not_found:[join $not_found ,]"
    }
    ::mcp_bridge::send_ok $channel $msg
}

# ---------------------------------------------------------------------------
# Start the bridge
# ---------------------------------------------------------------------------
::mcp_bridge::init

# When run via nohup (stdin=/dev/null), xmsim exits after -input script
# instead of entering interactive mode. vwait keeps the process alive
# and processes fileevent (socket) callbacks.
# Note: vwait in stopped state does NOT advance simulation.
# SimVision has its own GUI event loop that processes fileevent callbacks,
# so vwait would BLOCK it — only use vwait for xmsim.
puts "MCP Bridge: ready (waiting for client)"
if {![info exists ::mcp_bridge::_shutdown_flag]} {
    set ::mcp_bridge::_shutdown_flag 0
}
if {$::mcp_bridge::bridge_type eq "xmsim"} {
    vwait ::mcp_bridge::_shutdown_flag
} else {
    # SimVision's GUI event loop does not process Tcl socket events.
    # Use periodic 'update' to force-process pending fileevent/after callbacks.
    proc ::mcp_bridge::sv_event_pump {} {
        if {[info exists ::mcp_bridge::_shutdown_flag] && $::mcp_bridge::_shutdown_flag} {
            return
        }
        update
        after 50 ::mcp_bridge::sv_event_pump
    }
    puts "MCP Bridge: SimVision mode — starting event pump (50ms)"
    after 50 ::mcp_bridge::sv_event_pump
}

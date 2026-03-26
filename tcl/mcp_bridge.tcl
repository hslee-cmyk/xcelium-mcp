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
    variable cmd_buffer ""
    variable async_running 0
    variable async_done 0
    variable async_stop_reason ""
    variable watch_ids [list]
    variable _checkpoint_dir ""
    variable _checkpoint_name ""
}

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------
proc ::mcp_bridge::init {} {
    variable port
    variable server_socket

    # Allow port override via environment variable
    if {[info exists ::env(MCP_BRIDGE_PORT)]} {
        set port $::env(MCP_BRIDGE_PORT)
    }

    # Close existing server if re-sourced
    if {$server_socket ne ""} {
        catch {close $server_socket}
    }

    set server_socket [socket -server ::mcp_bridge::accept $port]
    puts "MCP Bridge: listening on port $port"

    # Signal readiness via file (avoids TCP client slot contention with ping loops)
    set ready_file "/tmp/mcp_bridge_ready_$port"
    if {[catch {
        set f [open $ready_file w]
        puts $f [clock seconds]
        close $f
    } err]} {
        puts "MCP Bridge: WARNING: could not create ready file: $err"
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
        set path [string trim [string range $cmd 8 end]]
        ::mcp_bridge::do_save $channel $path
        return
    }

    if {[string match "__RESTORE__*" $cmd]} {
        set path [string trim [string range $cmd 11 end]]
        ::mcp_bridge::do_restore $channel $path
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
        set path "/tmp/mcp_screenshot_[clock seconds].ps"
    }

    # Try SimVision waveform print first
    if {[catch {
        waveform print -file $path
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
# F1: __SHUTDOWN__ — safe shutdown (database close + finish)
# ---------------------------------------------------------------------------
proc ::mcp_bridge::do_shutdown {channel} {
    # 1. Close all SHM databases to flush data
    if {[catch {
        set dbs [database -list]
        foreach db $dbs {
            catch {database -close $db}
        }
    } err]} {
        # database -list may not be available; try known default
        catch {database -close ../dump/ci_top.shm}
    }

    # 2. Notify client before termination
    ::mcp_bridge::send_ok $channel "shutdown:ok"

    # 3. Schedule finish after returning to event loop
    #    (gives time for the OK response to be flushed)
    after 100 {finish}
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
proc ::mcp_bridge::do_save {channel name} {
    variable _checkpoint_dir
    variable _checkpoint_name

    if {$name eq ""} {
        set name "chk_[clock seconds]"
    }

    set dir "/tmp/mcp_checkpoints"
    file mkdir $dir

    if {[catch {save -simulation $name -path $dir -overwrite} err]} {
        ::mcp_bridge::send_error $channel "save failed: $err"
        return
    }

    set _checkpoint_dir $dir
    set _checkpoint_name $name

    ::mcp_bridge::send_ok $channel "saved:worklib.$name:module|dir:$dir"
}

proc ::mcp_bridge::do_restore {channel name} {
    variable _checkpoint_dir
    variable _checkpoint_name

    if {$name eq ""} {
        if {![info exists _checkpoint_name] || $_checkpoint_name eq ""} {
            ::mcp_bridge::send_error $channel "no checkpoint name given and no previous save"
            return
        }
        set name $_checkpoint_name
    }

    set dir "/tmp/mcp_checkpoints"
    if {[info exists _checkpoint_dir] && $_checkpoint_dir ne ""} {
        set dir $_checkpoint_dir
    }

    set snapshot "worklib.$name:module"

    if {[catch {restart $snapshot -path $dir} err]} {
        ::mcp_bridge::send_error $channel "restore failed: $err"
        return
    }

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

    set chk_dir "/tmp/mcp_bisect"
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
# Start the bridge
# ---------------------------------------------------------------------------
::mcp_bridge::init

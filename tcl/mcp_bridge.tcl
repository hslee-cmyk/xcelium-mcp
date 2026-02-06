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
# =============================================================================

namespace eval ::mcp_bridge {
    variable server_socket ""
    variable client_channel ""
    variable port 9876
    variable cmd_buffer ""
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

    # Try SimVision hardcopyPrint first
    if {[catch {
        # SimVision waveform hardcopy to PostScript
        hardcopyPrint -window waveform -format ps -file $path
    } err1]} {
        # Fallback: try X11 window capture via 'import' (ImageMagick)
        if {[catch {
            # Get SimVision main window ID
            set wid [winfo id .]
            exec import -window $wid $path
        } err2]} {
            ::mcp_bridge::send_error $channel \
                "Screenshot failed: hardcopyPrint=$err1, import=$err2"
            return
        }
    }

    ::mcp_bridge::send_ok $channel $path
}

# ---------------------------------------------------------------------------
# Start the bridge
# ---------------------------------------------------------------------------
::mcp_bridge::init

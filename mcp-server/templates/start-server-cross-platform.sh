#!/bin/bash
#
# ProjexLight Cross-Platform Server Startup Wrapper
# ============================================
# Automatically detects OS and runs appropriate script
# Works on Windows (Git Bash/WSL), macOS, and Linux

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"

# Detect operating system
detect_os() {
    case "$(uname -s)" in
        Linux*)
            if grep -q Microsoft /proc/version 2>/dev/null; then
                echo "wsl"
            else
                echo "linux"
            fi
            ;;
        Darwin*)
            echo "macos"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            echo "windows-bash"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

OS_TYPE=$(detect_os)
echo "Detected OS: $OS_TYPE"

case "$OS_TYPE" in
    windows-bash)
        # Running in Git Bash or similar on Windows
        # Prefer PowerShell if available
        if command -v powershell.exe &> /dev/null; then
            echo "Using PowerShell script..."
            powershell.exe -ExecutionPolicy Bypass -File "${SCRIPT_DIR}/start-server.ps1" -Action "$ACTION"
            exit $?
        else
            # Fallback to bash script (might have limited functionality on Windows)
            echo "PowerShell not found, using bash script..."
            bash "${SCRIPT_DIR}/start-server.sh" "$ACTION"
            exit $?
        fi
        ;;
    wsl)
        # Windows Subsystem for Linux
        # Can use either PowerShell (through .exe) or native bash
        if [ "$USE_NATIVE_BASH" = "true" ]; then
            bash "${SCRIPT_DIR}/start-server.sh" "$ACTION"
        else
            # Default: use PowerShell for consistency
            if command -v powershell.exe &> /dev/null; then
                powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w "${SCRIPT_DIR}/start-server.ps1")" -Action "$ACTION"
            else
                bash "${SCRIPT_DIR}/start-server.sh" "$ACTION"
            fi
        fi
        exit $?
        ;;
    linux|macos)
        # Native Unix systems - use bash script
        bash "${SCRIPT_DIR}/start-server.sh" "$ACTION"
        exit $?
        ;;
    *)
        echo "Unknown operating system. Attempting bash script..."
        bash "${SCRIPT_DIR}/start-server.sh" "$ACTION"
        exit $?
        ;;
esac

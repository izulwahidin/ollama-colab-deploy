import os
import subprocess
import time
import re
import urllib.request
import json
import sys
import shutil

# --- CONFIGURATION ---
MODEL_NAME = "qwen3.6:27b"  # Your primary targeted model
MODEL_NAME_WITH_CTX = f"{MODEL_NAME.split(':')[0]}:14b-16k"  # Model variant with extended context
OLLAMA_PORT = "11434"
OLLAMA_HOST = f"0.0.0.0:{OLLAMA_PORT}"
OLLAMA_EXECUTABLE_PATH = "/usr/local/bin/ollama"
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
CLOUDFLARED_PATH = "./cloudflared"
CONTEXT_WINDOW = 16384  # Extended context for agentic tools support


def run_system_command(command, description=None):
    """Helper to run system commands safely without shell=True."""
    if description:
        print(f"-> {description}...")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return result
    except subprocess.CalledProcessError as e:
        print(f"❌ Error during: {description or ' '.join(command)}")
        print(f"Command output: {e.stderr}")
        sys.exit(1)


def prepare_dependencies():
    """Checks and installs system-level requirements only if missing."""
    print("[1/5] Checking and initializing system dependencies...")

    # 1. Check for 'zstd'
    if shutil.which("zstd"):
        print("✅ Dependency 'zstd' is already installed.")
    else:
        print("-> 'zstd' missing. Installing...")
        run_system_command(["sudo", "apt-get", "update", "-y"], "Updating apt packages")
        run_system_command(["sudo", "apt-get", "install", "-y", "zstd"], "Installing zstd package")

    # 2. Check for Ollama Binary
    if os.path.exists(OLLAMA_EXECUTABLE_PATH):
        print("✅ Ollama framework is already installed.")
    else:
        print("-> Ollama framework missing. Installing via official script...")
        try:
            subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=True, stdout=subprocess.DEVNULL)
            print("✅ Ollama installation completed successfully.")
        except subprocess.CalledProcessError:
            print("❌ Critical Error: Ollama framework installation script failed.")
            sys.exit(1)


def pull_model_with_progress():
    """Streams pull requests from Ollama's local API endpoint to draw a live progress bar."""
    print(f"-> Downloading {MODEL_NAME} via streaming API...")
    url = f"http://localhost:{OLLAMA_PORT}/api/pull"
    data = json.dumps({"name": MODEL_NAME, "stream": True}).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req) as response:
            for line in response:
                if not line:
                    continue

                status_data = json.loads(line.decode("utf-8"))
                status = status_data.get("status", "")

                # Check if we have total and completed byte fields
                total = status_data.get("total", 0)
                completed = status_data.get("completed", 0)

                if total > 0:
                    percent = (completed / total) * 100
                    filled_length = int(40 * completed // total)
                    # Create a classic console progress bar [████████░░░░░░░░]
                    bar = "█" * filled_length + "░" * (40 - filled_length)

                    # Human readable sizes
                    completed_gb = completed / (1024**3)
                    total_gb = total / (1024**3)

                    # Print progress line dynamically using carriage return (\r)
                    sys.stdout.write(f"\r   [{bar}] {percent:.1f}% ({completed_gb:.2f}/{total_gb:.2f} GB) - {status}")
                    sys.stdout.flush()
                else:
                    # Fallback for states without byte metrics (e.g., 'verifying sha256')
                    sys.stdout.write(f"\r   -> {status}...{' ' * 30}")
                    sys.stdout.flush()
            print("\n✅ Model download complete.")
    except Exception as e:
        print(f"\n❌ Failed to communicate with Ollama API for pulling: {e}")
        sys.exit(1)


def setup_model_context_window():
    """
    Sets up extended context window for agentic tools support.
    This is critical: Ollama defaults to 4096 context even if models support more.
    We create a variant with num_ctx=16384 for proper tool usage.
    """
    print("[2/5] Configuring model context window for agentic tools...")

    # First, check if the base model is already pulled
    try:
        result = subprocess.run([OLLAMA_EXECUTABLE_PATH, "list"], capture_output=True, text=True, check=True)
        if MODEL_NAME not in result.stdout:
            print(f"-> Base model '{MODEL_NAME}' not found. Pulling...")
            pull_model_with_progress()
    except subprocess.CalledProcessError:
        print("⚠️ Warning: Could not verify installed models. Attempting to pull...")
        pull_model_with_progress()

    # Check if the extended context variant already exists
    try:
        result = subprocess.run([OLLAMA_EXECUTABLE_PATH, "list"], capture_output=True, text=True, check=True)
        if MODEL_NAME_WITH_CTX in result.stdout:
            print(f"✅ Model '{MODEL_NAME_WITH_CTX}' with extended context already configured.")
            return
    except subprocess.CalledProcessError:
        pass

    # Create extended context variant using interactive Ollama REPL
    print(f"-> Creating '{MODEL_NAME_WITH_CTX}' with num_ctx={CONTEXT_WINDOW}...")

    ollama_commands = f"/set parameter num_ctx {CONTEXT_WINDOW}\n/save {MODEL_NAME_WITH_CTX}\n/bye\n"

    try:
        process = subprocess.Popen(
            [OLLAMA_EXECUTABLE_PATH, "run", MODEL_NAME],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=ollama_commands, timeout=120)

        if process.returncode == 0 or "Created new model" in stdout:
            print(f"✅ Successfully created model variant '{MODEL_NAME_WITH_CTX}'")
            return MODEL_NAME_WITH_CTX
        else:
            print(f"⚠️ Warning: Could not create extended context variant. Using base model.")
            print(f"   Output: {stdout}")
            return MODEL_NAME
    except subprocess.TimeoutExpired:
        print(f"⚠️ Warning: Context window setup timed out. Using base model.")
        process.kill()
        return MODEL_NAME
    except Exception as e:
        print(f"⚠️ Warning: Failed to configure context window: {e}")
        return MODEL_NAME


def pull_model_if_missing():
    """Checks if the LLM model is already downloaded. If not, pulls it with status reporting."""
    # This is now handled by setup_model_context_window()
    pass


def setup_cloudflared():
    """Checks for cloudflared binary locally, downloads only if missing."""
    print("[3/5] Deploying secure tunnel gateway...")

    if os.path.exists(CLOUDFLARED_PATH) and os.access(CLOUDFLARED_PATH, os.X_OK):
        print("✅ Valid local 'cloudflared' binary found. Skipping download.")
        return

    try:
        print("-> Downloading latest cloudflared binary natively...")
        urllib.request.urlretrieve(CLOUDFLARED_URL, CLOUDFLARED_PATH)
        os.chmod(CLOUDFLARED_PATH, 0o755)  # Make executable
        print("✅ 'cloudflared' downloaded and ready.")
    except Exception as e:
        print(f"❌ Failed to download cloudflared: {e}")
        sys.exit(1)


def generate_opencode_config(public_url, effective_model):
    """
    Generates the opencode.json configuration file following p-lemonish reference.
    Minimal, clean config with tools enabled for extended-context models.

    Reference: https://github.com/p-lemonish/ollama-x-opencode
    """
    print("[5/5] Generating OpenCode configuration...")

    # Clean config format (matching p-lemonish reference)
    config_data = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": f"{public_url}/v1"
                },
                "models": {
                    effective_model: {
                        "tools": True
                    }
                }
            }
        }
    }

    try:
        file_path = "opencode.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        print(f"✅ Generated config at: {os.path.abspath(file_path)}")
        print(f"   📌 Primary model: {effective_model} (with agentic tools enabled)")
    except Exception as e:
        print(f"❌ Failed to write opencode.json: {e}")


def main():
    # 1. Environment & Framework Setup
    prepare_dependencies()

    # Configure environment variables for running the service
    ollama_env = os.environ.copy()
    ollama_env["OLLAMA_HOST"] = OLLAMA_HOST
    ollama_env["PATH"] = f"{ollama_env.get('PATH', '')}:/usr/local/bin"

    # Start Ollama service in the background
    print("-> Starting Ollama server background process...")
    ollama_proc = subprocess.Popen(
        [OLLAMA_EXECUTABLE_PATH, "serve"],
        env=ollama_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(4)  # Give server a moment to bind to the port

    # 2. Model Setup with Context Window Configuration (KEY FIX!)
    effective_model = setup_model_context_window()

    # 3. Tunnel Deployment (Conditional Download)
    setup_cloudflared()

    # 4. Tunnel Execution
    print("[4/5] Establishing secure tunnel connectivity...")
    public_url = None
    max_retries = 5
    tunnel_proc = None

    try:
        for attempt in range(max_retries):
            print(f"-> Attempting Cloudflare tunnel connection (Attempt {attempt + 1}/{max_retries})...")

            tunnel_proc = subprocess.Popen(
                [CLOUDFLARED_PATH, "tunnel", "--url", f"http://localhost:{OLLAMA_PORT}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            cloudflared_output = []
            for line in tunnel_proc.stdout:
                line_str = line.strip()
                cloudflared_output.append(line_str)
                if "trycloudflare.com" in line_str:
                    match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line_str)
                    if match:
                        public_url = match.group(0)
                        break

            if public_url:
                print(f"\n🚀 BACKEND LIVE: {public_url}")
                break
            else:
                print(f"❌ Tunnel attempt {attempt + 1} failed. Retrying in 5s...")
                if tunnel_proc:
                    tunnel_proc.terminate()
                time.sleep(5)

        if not public_url:
            print("❌ Critical Error: Cloudflare tunnel generation failed after maximum retries.")
            print("\n--- Last Cloudflare Tunnel Logs ---")
            print("\n".join(cloudflared_output[-20:]))
            sys.exit(1)

        # 5. Generate Opencode Configuration with tools support
        generate_opencode_config(public_url, effective_model)

        print("\n" + "="*60)
        print("✨ Setup Complete! You're ready to use agentic tools.")
        print("="*60)
        print("\nPress Ctrl+C to shut down the server and close the tunnel.")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down environments gracefully...")
    finally:
        # Clean up background tasks cleanly when exiting
        if tunnel_proc:
            tunnel_proc.terminate()
        if ollama_proc:
            ollama_proc.terminate()
        print("Goodbye!")


if __name__ == "__main__":
    main()

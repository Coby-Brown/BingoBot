#!/usr/bin/env python3
"""Start the realtime server with ngrok tunneling for external access."""

import subprocess
import sys
from pathlib import Path
from pyngrok import ngrok

def main():
    # Load ngrok token from Tokens file
    tokens_file = Path(__file__).parent / "Tokens"
    if not tokens_file.exists():
        print("Error: Tokens file not found.")
        print("Create a 'Tokens' file with: NGROK_TOKEN=your_token_here")
        sys.exit(1)
    
    token = None
    with open(tokens_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("NGROK_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
    
    if not token:
        print("Error: NGROK_TOKEN not found in Tokens file.")
        print("Add your token: NGROK_TOKEN=your_token_here")
        sys.exit(1)
    
    # Authenticate ngrok
    ngrok.set_auth_token(token)
    
    # Start ngrok tunnel to local server
    print("Starting ngrok tunnel...")
    public_url = ngrok.connect(8000, "http")
    print(f"✓ Public URL: {public_url}")
    print(f"✓ Local URL: http://localhost:8000/")
    print()
    print("Share the public URL with players outside your network.")
    print("Press Ctrl+C to stop the server.\n")
    
    # Start the Flask server
    try:
        subprocess.run([
            sys.executable, "realtime_server.py",
            "--web-card", "generated-bingo-card.html",
            "--host", "0.0.0.0",
            "--port", "8000"
        ])
    except KeyboardInterrupt:
        print("\nShutting down...")
        ngrok.kill()

if __name__ == "__main__":
    main()

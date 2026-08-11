import argparse

from resona import create_app


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Resona development server.")
    parser.add_argument("mode", nargs="?", choices=("debug",), help="Enable Flask debugging and detailed AI agent traces.")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    debug_mode = args.mode == "debug"
    app = create_app({"AGENT_TRACE": debug_mode})
    if debug_mode:
        print("[Resona Debug] Flask debug mode and AI agent tracing are enabled.", flush=True)
        print("[Resona Debug] User prompts, model responses, tool calls, results, and validation events will be printed. Secrets are redacted.", flush=True)
    app.run(debug=debug_mode, port=8080)
else:
    app = create_app()

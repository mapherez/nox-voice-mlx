"""Native CLI: setup, manual controller, model preparation and optional autostart."""

import argparse
import ipaddress
import ssl
import sys
from pathlib import Path

import httpx2 as httpx

from .config import DATA_DIR, MAC_ROOT, ControllerConfig, load_config, save_config


def main():
    parser = argparse.ArgumentParser(
        prog="nox-voice", description="NoX Voice native Mac controller"
    )
    parser.add_argument("--config", type=Path, default=DATA_DIR / "controller.json")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser(
        "setup", help="Create private controller configuration and gateway certificates"
    )
    setup.add_argument("--host", required=True, help="Reserved private LAN IP address of this Mac")
    setup.add_argument("--port", type=int, default=8443)
    setup.add_argument("--worker-port", type=int, default=8000)
    setup.add_argument("--weights-dir", type=Path, default=MAC_ROOT / "weights")
    sub.add_parser("controller", help="Run the controller manually; the model starts stopped")
    prepare = sub.add_parser("prepare-model", help="Download and verify the official model assets")
    prepare.add_argument("--weights-dir", type=Path, default=MAC_ROOT / "weights")
    prepare.add_argument("--revision", help="Optional full official Hugging Face commit hash")
    prepare.add_argument(
        "--check", action="store_true", help="Verify the existing manifest offline"
    )
    auto = sub.add_parser("autostart", help="Manage optional controller startup after macOS login")
    auto.add_argument("action", choices=["enable", "disable", "status"])
    args = parser.parse_args()
    try:
        if args.command == "setup":
            if args.config.exists():
                raise ValueError(
                    "Configuration already exists. Keep it or select a new --config path for a new installation."
                )
            config = ControllerConfig(
                host=args.host,
                port=args.port,
                worker_port=args.worker_port,
                weights_dir=args.weights_dir.resolve(),
                data_dir=args.config.parent.resolve(),
            )
            if config.port == config.worker_port:
                raise ValueError("Controller and worker ports must be different.")
            from .certificates import create_certificates

            bundle = create_certificates(config.tls_dir, config.host)
            save_config(args.config, config)
            print(f"Controller configuration created: {args.config}")
            host = f"[{config.host}]" if ":" in config.host else config.host
            print(f"Gateway URL: https://{host}:{config.port}")
            print(f"Copy ONLY this client directory to the homelab secrets directory: {bundle}")
            print("Keep the CA private key and server private key on this Mac.")
            print(
                "Client/server certificates expire in one year. Rotate using a new setup directory before expiry."
            )
        elif args.command == "prepare-model":
            from .model_files import prepare_model, verify_model

            manifest = (
                verify_model(args.weights_dir)
                if args.check
                else prepare_model(args.weights_dir, args.revision)
            )
            print(f"Model verified at official revision {manifest['revision']}.")
        elif args.command == "autostart":
            from . import autostart

            if args.action == "enable":
                load_config(args.config)
                autostart.enable(args.config)
            elif args.action == "disable":
                autostart.disable()
            print(
                "Automatic controller startup is "
                + ("enabled." if autostart.is_enabled() else "disabled.")
            )
        else:
            if sys.platform != "darwin":
                raise ValueError("The native controller must run on macOS.")
            config = load_config(args.config)
            address = ipaddress.ip_address(config.host)
            if not address.is_private or address.is_unspecified or address.is_multicast:
                raise ValueError("Bind the controller to a specific private IP address.")
            import uvicorn

            from .server import create_app

            print(
                f"NoX Voice controller: https://{config.host}:{config.port} (client certificate required)"
            )
            print("Engine is stopped. Start it from the webapp Settings drawer.")
            uvicorn.run(
                create_app(config),
                host=config.host,
                port=config.port,
                ssl_keyfile=str(config.tls_dir / "server.key"),
                ssl_certfile=str(config.tls_dir / "server.crt"),
                ssl_ca_certs=str(config.tls_dir / "ca.crt"),
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                access_log=False,
                proxy_headers=False,
                ws_max_size=8192,
                ws_max_queue=4,
                ws_ping_interval=20,
                ws_ping_timeout=20,
            )
    except (ValueError, OSError, ImportError, httpx.HTTPError) as error:
        if args.command == "prepare-model" and not isinstance(error, ValueError):
            parser.exit(
                1,
                "Model preparation failed. Install the engine extra, accept the official model terms and configure HF_TOKEN.\n",
            )
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

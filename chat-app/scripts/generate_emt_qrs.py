#!/usr/bin/env python3
import argparse
from pathlib import Path

from generate_qr import generate


SERVICES = (
    ("emt-bus-stop", "Parada 72 · Cibeles", "Paradas / Tiempo real", "bus"),
    ("emt-tickets", "Billetes EMT", "Billetes / Demo", "ticket"),
    ("bicimad", "BiciMAD", "Bicicletas / Madrid", "bike"),
)


def main():
    parser = argparse.ArgumentParser(description="Generate posters for the three EMT demo services.")
    parser.add_argument("--base-url", default="https://emt.searchyourcollection.com")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[2] / "qr-codes")
    args = parser.parse_args()
    for server_id, label, subtitle, icon in SERVICES:
        output = args.output_dir / f"{server_id}.svg"
        generate(f"{args.base_url.rstrip('/')}/m/{server_id}", label, subtitle, icon, output)
        print(output)


if __name__ == "__main__":
    main()
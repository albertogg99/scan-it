import argparse

import qrcode

parser = argparse.ArgumentParser(description="Turn text into a QR code PNG.")
parser.add_argument("text")
parser.add_argument("-o", "--output", default="qr.png")
args = parser.parse_args()

qrcode.make(args.text).save(args.output)
print(f"Saved {args.output}")

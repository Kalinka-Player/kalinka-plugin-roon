#!/usr/bin/env python3
"""Inspect the paired extension over SSH, without copying pairing credentials."""

import argparse
import json
import socket
import subprocess

REMOTE = """
import json, socket, sys
request = sys.stdin.buffer.readline()
with socket.socket(socket.AF_UNIX) as client:
    client.settimeout(12)
    client.connect(sys.argv[1])
    client.sendall(request)
    with client.makefile('r') as reader:
        print(reader.readline(), end='')
"""


def rpc(
    host,
    method,
    *,
    socket_path="/var/lib/kalinka/roon/pairing/control.sock",
    **arguments,
):
    import shlex

    request = json.dumps({"id": 1, "method": method, **arguments}) + "\n"
    if host == "local":
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(12)
            client.connect(socket_path)
            client.sendall(request.encode())
            reply = json.loads(client.makefile().readline())
    else:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                host,
                "sudo python3 -c "
                + shlex.quote(REMOTE)
                + " "
                + shlex.quote(socket_path),
            ],
            input=request,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        reply = json.loads(result.stdout)
    if reply.get("error"):
        raise RuntimeError(reply["error"])
    return reply


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", nargs="?", default="snapshot")
    parser.add_argument("arguments", nargs="?", default="{}")
    parser.add_argument("--host", default="raspberrypi.local")
    parser.add_argument(
        "--socket", default="/var/lib/kalinka/roon/pairing/control.sock"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            rpc(
                args.host,
                args.method,
                socket_path=args.socket,
                **json.loads(args.arguments),
            ),
            indent=2,
        )
    )

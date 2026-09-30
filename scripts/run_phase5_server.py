"""Development launcher: optional explicit credential file, then normal ASGI app."""
import argparse
from pathlib import Path

import uvicorn

from scripts.model_env import load_credentials_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file",type=Path)
    parser.add_argument("--port",type=int,default=8000)
    args = parser.parse_args()
    load_credentials_file(args.credentials_file)
    uvicorn.run("backend.app.main:app",host="127.0.0.1",port=args.port)


if __name__ == "__main__":
    main()

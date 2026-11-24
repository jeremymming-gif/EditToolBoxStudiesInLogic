"""Fail the synthetic child processes immediately if they try external AI or sockets."""

import os
import sys


class BlockExternalAi:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "slcn.llm" or fullname.startswith("slcn.llm."):
            os._exit(91)
        return None


def reject_forbidden_access(event, args):
    if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname"}:
        os._exit(92)


sys.meta_path.insert(0, BlockExternalAi())
sys.addaudithook(reject_forbidden_access)

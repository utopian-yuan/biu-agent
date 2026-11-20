"""Bind a Bash text pipeline to the files visible in the current conversation."""

import re
import shlex
import shutil
import subprocess

from ...identity import Problem, hidden


def syntax():
    return Problem(
        422,
        "bash_syntax",
        "支持 pwd、ls [目录]、echo 文本，以及 cat、head、tail、grep -F、sort、uniq、wc 的文本管道。",
    )


def binary(name):
    path = shutil.which(name, path="/usr/bin:/bin")
    if not path:
        raise Problem(503, "command_unavailable", "本机缺少命令 " + name)
    return path


def stage(words):
    if not words:
        raise syntax()
    name, *args = words
    if name in {"head", "tail"}:
        if args:
            if (
                len(args) != 2
                or args[0] != "-n"
                or not re.fullmatch(r"[0-9]{1,3}", args[1])
                or not 1 <= int(args[1]) <= 200
            ):
                raise syntax()
    elif name == "grep":
        insensitive = False
        while len(args) > 1 and args[0] in {"-F", "-i", "-e", "--"}:
            option = args.pop(0)
            insensitive |= option == "-i"
            if option in {"-e", "--"}:
                break
        if len(args) != 1 or not 1 <= len(args[0]) <= 120:
            raise syntax()
        args = ["-F", *(["-i"] if insensitive else []), "-e", args[0]]
    else:
        flags = {"cat": "", "wc": "lwc", "sort": "rnu", "uniq": "c"}
        if name not in flags or any(
            not (a.startswith("-") and a[1:] and set(a[1:]) <= set(flags[name])) for a in args
        ):
            raise syntax()
    return shlex.join([binary(name), *args])


def split_pipeline(command):
    if any(ord(c) < 32 for c in command):
        raise syntax()
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        groups = [[]]
        for word in lexer:
            if word == "|":
                groups.append([])
            else:
                groups[-1].append(word)
    except ValueError:
        raise syntax() from None
    if len(groups) > 5 or not all(groups):
        raise syntax()
    return groups


def relative(name):
    if name in {".", "/workspace", "/workspace/"}:
        return ""
    return name.removeprefix("/workspace/").removeprefix("./")


def list_directory(name, paths):
    name = relative(name).rstrip("/")
    if not name:
        return "materials/\nuploads/\nwork/\n"
    entries = set()
    for path in paths():
        if path == name:
            return path + "\n"
        if path.startswith(name + "/"):
            child, slash, _ = path[len(name) + 1 :].partition("/")
            entries.add(child + slash)
    if not entries and name not in {"materials", "uploads", "work"}:
        raise hidden()
    return "".join(entry + "\n" for entry in sorted(entries))


def pipeline(command, path, paths, document):
    groups = split_pipeline(command)
    first = groups[0]
    name, *args = first
    content = ""
    if name in {"pwd", "ls", "echo"}:
        if path:
            raise syntax()
        if name == "pwd" and not args:
            content = "/workspace\n"
        elif name == "ls" and len(args) <= 1 and not (args and args[0].startswith("-")):
            content = list_directory(args[0] if args else ".", paths)
        elif name == "echo":
            compact = args[:1] == ["-n"]
            content = " ".join(args[1:] if compact else args) + ("" if compact else "\n")
        else:
            raise syntax()
        groups[0] = ["cat"]
    else:
        # A final file operand belongs to our virtual workspace. Only validated filter arguments reach Bash.
        try:
            stage(first)
        except Problem as error:
            if error.code != "bash_syntax" or path or not args or args[-1].startswith("-"):
                raise
            stage(first[:-1])
            path = first.pop()
        if path:
            doc = document(relative(path))
            content = doc["body"] if "body" in doc else "\n".join(doc["sections"].values())
    # Reconstruct exclusively from validated argv. User-supplied shell source is never evaluated.
    script = " | ".join(stage(group) for group in groups)
    try:
        proc = subprocess.run(
            [binary("bash"), "--noprofile", "--norc", "-o", "pipefail", "-c", script],
            input=content,
            encoding="utf-8",
            capture_output=True,
            timeout=3,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Problem(503, "bash_failed", "文本管道未在规定时间内完成。") from None
    if proc.returncode not in (0, 1, 141) or proc.stderr:
        raise Problem(422, "bash_failed", "文本管道未能完成，请检查命令参数。")
    return proc.stdout

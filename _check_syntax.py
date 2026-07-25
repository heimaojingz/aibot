import py_compile
import sys

files = [
    "database/__init__.py",
    "config.py",
    "keyboards/__init__.py",
    "handlers/captcha.py",
    "handlers/blacklist.py",
    "handlers/ai.py",
    "handlers/quiet_mode.py",
    "services/ai_service.py",
    "services/spam_detector.py",
    "web/routes.py",
    "handlers/__init__.py",
    "main.py",
]

ok = 0
fail = 0
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK  {f}")
        ok += 1
    except py_compile.PyCompileError as e:
        print(f"FAIL  {f}: {e}")
        fail += 1

print(f"\n{ok} passed, {fail} failed")

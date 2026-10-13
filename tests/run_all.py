"""Dependency-free test runner: python -m tests.run_all. (pytest also works: pytest -q)"""
import importlib, pkgutil, sys, time, traceback, unittest
import tests

def main() -> int:
    ok = fail = skip = 0
    t0 = time.time()
    for m in sorted(pkgutil.iter_modules(tests.__path__), key=lambda m: m.name):
        if not m.name.startswith("test_"):
            continue
        mod = importlib.import_module(f"tests.{m.name}")
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            try:
                getattr(mod, name)()
                ok += 1
            except unittest.SkipTest as e:
                skip += 1
                print(f"SKIP {m.name}.{name}: {e}")
            except Exception:
                fail += 1
                print(f"FAIL {m.name}.{name}")
                traceback.print_exc()
    print(f"\n{ok} passed, {fail} failed, {skip} skipped in {time.time()-t0:.1f}s")
    return 1 if fail else 0

if __name__ == "__main__":
    sys.exit(main())

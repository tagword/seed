"""
Seed CLI — 简单编排入口。

用法:
    seed info         查看各子包版本
    seed check        检查各子包是否可正常导入
"""

import importlib
import sys


def _import_check(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def cmd_info():
    """查看各子包版本。"""
    pkgs = {
        "seed.core": "seed.core",
        "seed.integrations": "seed.integrations",
    }
    print("📦 Seed (kernel + integrations)\n")
    for display, mod_name in pkgs.items():
        try:
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", "?")
            status = "✅"
        except ImportError:
            ver = "—"
            status = "❌"
        print(f"  {status}  {display:<22}  v{ver}")

    try:
        import seed_tools  # noqa: F401

        v = getattr(seed_tools, "__version__", "?")
        print(f"  ✅  seed_tools (optional)   v{v}")
    except ImportError:
        print("  —  seed_tools (optional)   not installed")

    try:
        import seed  # noqa: F811

        print(f"\n  🎯  seed (top-level)      v{seed.__version__}")
    except ImportError:
        print("\n  🎯  seed (top-level)      —")


def cmd_check():
    """检查各组件导入是否正常。"""
    modules = [
        "seed.core",
        "seed.core.tool_runtime",
        "seed.core.agent_runtime",
        "seed.core.llm_sess",
        "seed.core.mem_sys",
        "seed.core.engine",
        "seed.core.turn_loop",
        "seed.core.sess_store",
        "seed.integrations",
        "seed.integrations.safety",
        "seed.integrations.webhook_dedup",
        "seed.models",
    ]
    all_ok = True
    print("🔍 导入检查\n")
    for mod in modules:
        ok = _import_check(mod)
        if not ok:
            all_ok = False
        print(f"  {'✅' if ok else '❌'}  {mod}")

    print()
    ok_tools = _import_check("seed_tools")
    if not ok_tools:
        all_ok = False
    print(f"  {'✅' if ok_tools else '❌'}  seed_tools (install seed-tools if missing)")
    sys.exit(0 if all_ok else 1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return
    cmd = sys.argv[1]
    if cmd == "info":
        cmd_info()
    elif cmd == "check":
        cmd_check()
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

"""
Seed CLI — 简单编排入口。

用法:
    seed info         查看各组件版本
    seed check        检查各组件是否可正常导入
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
    """查看各组件版本。"""
    pkgs = {
        "seed-engine": "seed_engine",
        "seed-services": "seed_services",
        "seed-tools": "seed_tools",
    }
    print("📦 Seed Components\n")
    for display, mod_name in pkgs.items():
        try:
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", "?")
            status = "✅"
        except ImportError:
            ver = "—"
            status = "❌"
        print(f"  {status}  {display:<18}  v{ver}")

    # meta-package itself
    try:
        import seed  # noqa: F811
        print(f"\n  🎯  seed (meta)          v{seed.__version__}")
    except ImportError:
        print("\n  🎯  seed (meta)          —")


def cmd_check():
    """检查各组件导入是否正常。"""
    modules = [
        "seed_engine",
        "seed_engine.agent_runtime",
        "seed_engine.llm_sess",
        "seed_engine.mem_sys",
        "seed_engine.safety",
        "seed_engine.engine",
        "seed_engine.turn_loop",
        "seed_services",
        "seed_services.browser",
        "seed_services.safety",
        "seed_services.webhook_dedup",
        "seed_tools",
        "seed_tools.registry",
        "seed_tools.executor",
        "seed.models",
    ]
    all_ok = True
    print("🔍 组件导入检查\n")
    for mod in modules:
        ok = _import_check(mod)
        if not ok:
            all_ok = False
        print(f"  {'✅' if ok else '❌'}  {mod}")
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
        print(f"未知命令: {cmd}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()

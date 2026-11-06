"""Web UI 打包入口；模型和配置由 utils.paths 相对 EXE 解析。"""
if __name__ == '__main__':
    # 必须早于 numpy/scipy：限制 OpenBLAS 线程缓冲（详见 utils/env_limits.py）
    from utils.env_limits import apply_runtime_limits
    apply_runtime_limits()

    import multiprocessing
    multiprocessing.freeze_support()
    import sys
    from qt_web.bootstrap import initialize
    initialize()
    if '--self-test' in sys.argv:
        from qt_web.release_gui_check import gui_check
        gui_check(sys.argv[sys.argv.index('--self-test')+1])
    else:
        from qt_web.main import main
        main()

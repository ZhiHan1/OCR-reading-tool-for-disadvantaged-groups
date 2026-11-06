"""控制台 worker 保留 stdin/stdout 管道，供无控制台 GUI 调用。"""
if __name__ == '__main__':
    # 必须早于 numpy/torch：限制 OpenBLAS 线程缓冲（详见 utils/env_limits.py）
    from utils.env_limits import apply_runtime_limits
    apply_runtime_limits()

    import multiprocessing
    multiprocessing.freeze_support()
    import sys
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if '--self-test' in sys.argv:
        from qt_web.release_worker_check import worker_check
        worker_check(sys.argv[sys.argv.index('--self-test')+1])
    else:
        from qt_web.voice_worker import main
        main()

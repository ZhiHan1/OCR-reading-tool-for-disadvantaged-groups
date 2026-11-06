"""可测试的端点策略。短停顿分段 ≠ 一轮发言结束。"""
MODES = ('manual', 'realtime', 'auto')


def settings(config):
    mode = str(config.get('asr', 'input_mode', default='manual'))
    if mode not in MODES:
        mode = 'manual'
    return dict(mode=mode, segment_silence_ms=int(config.get('asr', 'segment_silence_ms', default=800)),
                turn_silence_ms=int(config.get('asr', 'turn_silence_ms', default=3000)),
                initial_silence_ms=15000)


class TurnDetector:
    def __init__(self, mode, silence_ms=3000, initial_ms=15000):
        self.mode = mode
        self.silence_ms = silence_ms
        self.initial_ms = initial_ms
        self.in_speech = False
        self.heard_speech = False
        self.last_end_ms = None

    def events(self, boundaries):
        for start, end in boundaries:
            if start >= 0:
                self.in_speech = True
                self.heard_speech = True
            if end >= 0:
                self.in_speech = False
                self.last_end_ms = end

    def should_stop(self, processed_ms):
        if self.mode != 'auto' or self.in_speech:
            return False
        if not self.heard_speech:
            return processed_ms >= self.initial_ms
        return self.last_end_ms is not None and processed_ms - self.last_end_ms >= self.silence_ms

"""在当前角色空闲时请求一次更新提醒，成功回复后记录当天已提醒的版本。"""
from datetime import datetime
import json
import os
import threading
import time


class UpdateAnnouncement:
    def __init__(self, chat, bundles, enabled, log, clock=time.monotonic):
        self.chat, self.bundles, self.enabled, self.log = chat, bundles, enabled, log
        self.clock = clock
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.operation = None
        self.attempted = None
        self.idle_key = None
        self.idle_since = None
        self.marker = bundles.directory / 'announced.json'
        self.announced = None
        try:
            if self.marker.exists():
                self.announced = json.loads(self.marker.read_text(encoding='utf-8'))
        except (ValueError, OSError) as error:
            self.log('warning', '无法读取整合包更新提醒记录', diagnostic=str(error))

    def start(self):
        self.thread = threading.Thread(target=self._run, name='sakuratts-update-notice', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.wait(.5):
            try:
                self.tick()
            except Exception as error:
                self.log('warning', '整合包更新提醒失败', diagnostic=str(error))
                return

    def tick(self):
        package = self.bundles.available
        if (self.stop.is_set() or not self.enabled() or not package or not self.bundles.current()
                or self.bundles._is_current(package)):
            self.idle_since = None
            return
        notice = {'releaseId': package['releaseId'], 'date': datetime.now().astimezone().date().isoformat()}
        if notice == self.announced or notice == self.attempted:
            return
        facts = self.chat.current()
        key = (facts['sessionId'], facts['activityRevision'], facts['interactionRevision'])
        if not facts['idle'] or not facts['sessionId']:
            self.idle_since = None
            return
        if key != self.idle_key or self.idle_since is None:
            self.idle_key, self.idle_since = key, self.clock()
            return
        if self.clock() - self.idle_since < 3:
            return
        prompt = '请提醒用户 SakuraTTS 有更新，可以去插件设置里更新。'
        with self.lock:
            if (self.stop.is_set() or not self.enabled() or self.bundles._is_current(package)
                    or self.bundles.available != package):
                return
            result = self.chat.submit({'sessionId': facts['sessionId'], 'message': prompt, 'resources': [],
                                       'notification': {'kind': 'update', 'text': 'SakuraTTS 有可用更新'}})
            if result['accepted']:
                self.operation = result['operationId'], notice
                self.attempted = notice
            elif result['reasonCode'] not in {'CHAT_BUSY', 'CHAT_SESSION_STALE', 'CHAT_ADMISSION_EXPIRED',
                                              'CHAT_EXECUTION_LIMIT_EXCEEDED'}:
                self.attempted = notice
                self.log('warning', '未能发起整合包更新提醒', reason_code=result['reasonCode'])
            self.idle_since = None

    def completed(self, event):
        with self.lock:
            if self.operation is None or event.get('operationId') != self.operation[0]:
                return
            notice = {**self.operation[1], 'date': datetime.now().astimezone().date().isoformat()}
            temporary = self.marker.with_suffix('.tmp')
            temporary.write_text(json.dumps(notice), encoding='utf-8')
            os.replace(temporary, self.marker)
            self.announced = notice
            self.operation = None

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
        with self.lock:
            if self.operation:
                self.chat.cancel(self.operation[0])
                self.operation = None

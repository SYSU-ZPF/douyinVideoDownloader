import re
import os
import threading
import queue
import time
import requests
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from tkinter.scrolledtext import ScrolledText
from playwright.sync_api import sync_playwright

class PlaywrightDouyinDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("抖音无水印视频增强下载器 (Playwright版)")
        self.root.geometry("760x620")
        self.events = queue.Queue()
        self.running = False
        self.ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        self.pc_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        tk.Label(root, text="粘贴多条抖音分享文本或 HTTP/HTTPS 链接（自动去重）").pack(anchor="w", padx=20, pady=(16, 4))
        self.url_entry = ScrolledText(root, height=8, wrap=tk.WORD)
        self.url_entry.pack(fill=tk.BOTH, expand=True, padx=20)
        buttons = tk.Frame(root)
        buttons.pack(pady=12)
        self.download_btn = tk.Button(buttons, text="开始批量下载", command=self.start_download_thread)
        self.download_btn.pack(side=tk.LEFT, padx=10)
        self.clear_btn = tk.Button(buttons, text="清空输入", command=self.clear_input)
        self.clear_btn.pack(side=tk.LEFT, padx=10)
        self.status_label = tk.Label(root, text="就绪", anchor="w")
        self.status_label.pack(fill=tk.X, padx=20)
        self.batch_label = tk.Label(root, text="整体进度：0 / 0", anchor="w")
        self.batch_label.pack(fill=tk.X, padx=20)
        self.batch_progress = ttk.Progressbar(root, mode="determinate")
        self.batch_progress.pack(fill=tk.X, padx=20, pady=4)
        self.file_label = tk.Label(root, text="当前视频：等待下载", anchor="w")
        self.file_label.pack(fill=tk.X, padx=20)
        self.file_progress = ttk.Progressbar(root, mode="determinate")
        self.file_progress.pack(fill=tk.X, padx=20, pady=4)
        self.log = ScrolledText(root, height=8, state=tk.DISABLED, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 16))
        self.root.after(100, self.poll_events)

    def clear_input(self):
        self.url_entry.delete("1.0", tk.END)

    def update_status(self, text, color="black"):
        self.status_label.config(text=text, fg=color)

    @staticmethod
    def extract_urls(text):
        """提取分享文本中的所有链接，去掉末尾中文标点，按出现顺序去重。"""
        matches = re.findall(r"https?://[^\s<>\"'，。！？；：、（）【】《》“”‘’]+", text, re.IGNORECASE)
        return list(dict.fromkeys(url.rstrip(".,!?;:)]}") for url in matches))

    def extract_url(self, text):
        urls = self.extract_urls(text)
        return urls[0] if urls else None

    def poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "status":
                    self.update_status(data, "blue")
                    self.file_progress.stop()
                    self.file_progress.config(mode="indeterminate", value=0)
                    self.file_progress.start(12)
                    self.file_label.config(text="当前视频：正在解析…")
                elif kind == "progress":
                    downloaded, total = data
                    self.file_progress.stop()
                    if total:
                        self.file_progress.config(mode="determinate", maximum=total, value=downloaded)
                        info = f"{downloaded / total:.1%} · {downloaded / 1048576:.2f} / {total / 1048576:.2f} MB"
                    else:
                        self.file_progress.config(mode="indeterminate")
                        self.file_progress.start(12)
                        info = f"已下载 {downloaded / 1048576:.2f} MB（总大小未知）"
                    self.file_label.config(text="当前视频：" + info)
                elif kind == "item":
                    completed, total, message = data
                    self.file_progress.stop()
                    self.batch_progress.config(maximum=total, value=completed)
                    self.batch_label.config(text=f"整体进度：{completed} / {total}")
                    self.log.config(state=tk.NORMAL)
                    self.log.insert(tk.END, message + "\n")
                    self.log.see(tk.END)
                    self.log.config(state=tk.DISABLED)
                elif kind == "done":
                    success, failures, directory = data
                    self.running = False
                    self.download_btn.config(state=tk.NORMAL)
                    self.clear_btn.config(state=tk.NORMAL)
                    self.file_progress.stop()
                    self.file_progress.config(mode="determinate", maximum=1, value=1 if not failures else 0)
                    self.file_label.config(text="当前批次已结束")
                    summary = f"下载结束：成功 {success}，失败 {failures}"
                    self.update_status(summary, "green" if not failures else "red")
                    messagebox.showinfo("批量下载完成", f"{summary}\n保存目录：{directory}\n详细结果见下方日志。")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def resolve_video_id(self, share_url):
        """解析短链接重定向地址，提取视频/图集 ID"""
        location = ""
        try:
            res = requests.get(share_url, headers={"User-Agent": self.ua}, timeout=10, allow_redirects=False)
            location = res.headers.get("Location", "")
        except Exception:
            pass

        match = re.search(r'/(?:video|note)/(\d+)', location) or re.search(r'/(?:video|note)/(\d+)', share_url)
        return match.group(1) if match else None

    def fetch_video_with_playwright(self, share_url):
        """通过 Playwright 拦截底层视频流资源"""
        captured_video_url = None
        video_title = None

        # 移动端分享页已不渲染 <video> 且数据接口返回参数不合法，
        # 改为打开桌面端 www.douyin.com/video/{id} 页面，拦截其详情接口
        video_id = self.resolve_video_id(share_url)
        target_url = f"https://www.douyin.com/video/{video_id}" if video_id else share_url

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=self.pc_ua,
                viewport={"width": 1380, "height": 900}
            )
            page = context.new_page()

            def extract_play_urls(detail):
                """从详情数据中提取所有可用的播放地址（优先无水印）"""
                video = detail.get("video", {}) or {}
                urls = list(video.get("play_addr", {}).get("url_list") or [])
                for br in video.get("bit_rate") or []:
                    urls += list(br.get("play_addr", {}).get("url_list") or [])
                urls += list(video.get("download_addr", {}).get("url_list") or [])
                # 兜底：用 uri 自行构造播放接口直链
                uri = video.get("play_addr", {}).get("uri")
                if uri:
                    urls.append(f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0")
                return urls

            def handle_response(response):
                nonlocal captured_video_url, video_title
                url = response.url

                # 首选：拦截详情接口，提取无水印 play_addr 与标题
                if "/aweme/v1/web/aweme/detail" in url:
                    try:
                        detail = response.json().get("aweme_detail") or {}
                        if detail.get("aweme_id"):
                            video_title = detail.get("desc") or video_title
                            if not captured_video_url:
                                play_urls = extract_play_urls(detail)
                                if play_urls:
                                    captured_video_url = play_urls[0]
                    except Exception:
                        pass

                # 兜底：仅捕获抖音 CDN 视频流，排除静态资源站占位视频
                if not captured_video_url:
                    is_cdn_stream = (
                        "douyinvod.com" in url
                        or "/video/tos/" in url
                        or "aweme/v1/play" in url
                    )
                    is_static_asset = any(
                        d in url for d in ("douyinstatic.com", "douyinpic.com", "bytedance.com", "zijieapi.com", "ibytedapm.com")
                    )
                    if is_cdn_stream and not is_static_asset:
                        captured_video_url = url

            page.on("response", handle_response)

            # 访问链接
            page.goto(target_url, wait_until="domcontentloaded", timeout=20000)

            # 轮询等待截获（最多 20 秒）
            for _ in range(40):
                if captured_video_url:
                    break
                page.wait_for_timeout(500)

            # 若仍未截获，强制触发视频播放
            if not captured_video_url:
                try:
                    page.evaluate("document.querySelector('video')?.play()")
                    for _ in range(10):
                        if captured_video_url:
                            break
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            # 标题兜底：取页面 <title>
            if not video_title:
                try:
                    raw_title = page.title()
                    if raw_title:
                        video_title = raw_title.split('-')[0].strip()
                except Exception:
                    pass

            browser.close()

        # 清洗标题（含换行等不可用作文件名的字符）
        video_title = re.sub(r'[\\/:*?"<>|\r\n]', '_', (video_title or "").strip()) or "douyin_video"
        return video_title, captured_video_url

    def download_file(self, video_url, save_path):
        """流式下载到临时文件，完成后再改名，失败不留下损坏的 MP4。"""
        headers = {'User-Agent': self.ua, 'Referer': 'https://www.douyin.com/'}
        partial = save_path + ".part"
        downloaded = 0
        last_update = 0
        try:
            with requests.get(video_url, headers=headers, stream=True, timeout=(10, 30)) as res:
                res.raise_for_status()
                try:
                    total = int(res.headers.get("Content-Length", 0))
                except (ValueError, TypeError):
                    total = 0
                if res.headers.get("Content-Encoding", "identity") != "identity":
                    total = 0
                self.events.put(("progress", (0, total)))
                with open(partial, 'wb') as f:
                    for chunk in res.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if now - last_update >= 0.1:
                                self.events.put(("progress", (downloaded, total)))
                                last_update = now
                if not downloaded:
                    raise ValueError("服务器返回空文件")
                if total and downloaded != total:
                    raise ValueError("下载不完整，请重试")
                os.replace(partial, save_path)
                self.events.put(("progress", (downloaded, total)))
            return True
        finally:
            if os.path.exists(partial):
                os.remove(partial)

    def start_download_thread(self):
        """所有 Tk 操作留在主线程，工作线程通过队列报告进度。"""
        if self.running:
            return
        urls = self.extract_urls(self.url_entry.get("1.0", tk.END))
        if not urls:
            messagebox.showwarning("提示", "请粘贴包含 HTTP/HTTPS 链接的分享文本！")
            return
        directory = filedialog.askdirectory(title=f"选择 {len(urls)} 个视频的保存目录")
        if not directory:
            return
        self.running = True
        self.download_btn.config(state=tk.DISABLED)
        self.clear_btn.config(state=tk.DISABLED)
        self.batch_progress.config(maximum=len(urls), value=0)
        self.batch_label.config(text=f"整体进度：0 / {len(urls)}")
        threading.Thread(target=self.process_download, args=(urls, directory), daemon=True).start()

    def process_download(self, urls, directory):
        success = 0
        failures = 0
        for index, share_url in enumerate(urls, 1):
            self.events.put(("status", f"[{index}/{len(urls)}] 正在解析：{share_url}"))
            try:
                title, video_url = self.fetch_video_with_playwright(share_url)
                if not video_url:
                    raise ValueError("未截获视频流，可能遇到验证码或视频已下架")
                stem = title[:50].rstrip(". ") or "douyin_video"
                save_path = os.path.join(directory, stem + ".mp4")
                suffix = 2
                while os.path.exists(save_path) or os.path.exists(save_path + ".part"):
                    save_path = os.path.join(directory, f"{stem}_{suffix}.mp4")
                    suffix += 1
                if not self.download_file(video_url, save_path):
                    raise ValueError("下载失败")
                success += 1
                message = f"[{index}/{len(urls)}] 成功：{save_path}"
            except Exception as exc:
                failures += 1
                message = f"[{index}/{len(urls)}] 失败：{share_url} — {exc}"
            self.events.put(("item", (index, len(urls), message)))
        self.events.put(("done", (success, failures, directory)))


if __name__ == "__main__":
    root = tk.Tk()
    app = PlaywrightDouyinDownloader(root)
    root.mainloop()

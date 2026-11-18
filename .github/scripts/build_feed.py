"""将 TrendRadar 生成的 index.html 转换为标准 Atom feed.xml 并整理 GitHub Pages 产物。"""

from __future__ import annotations

import html
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ATOM_NS = "http://www.w3.org/2005/Atom"
SITE_BASE_URL = "https://burn1ngz828.github.io/TrendRadar"
FEED_URL = f"{SITE_BASE_URL}/feed.xml"
MAX_ENTRIES = 21
CST = timezone(timedelta(hours=8))


class TrendReportParser(HTMLParser):
    """基于栈状态提取 TrendRadar HTML 报告的 AI 分析、关键词分组与独立热榜。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.ai_blocks: list[tuple[str, str]] = []
        self.word_groups: list[tuple[str, list[dict[str, str]]]] = []
        self.standalone_groups: list[tuple[str, list[dict[str, str]]]] = []

        self._cur_ai_title: list[str] = []
        self._cur_ai_content: list[str] = []
        self._cur_word_name: list[str] = []
        self._cur_standalone_name: list[str] = []
        self._cur_group_items: list[dict[str, str]] = []
        self._cur_item: dict[str, str] | None = None
        self._cur_item_source: list[str] = []
        self._cur_item_rank: list[str] = []
        self._cur_item_title: list[str] = []
        self._capture_target: str | None = None

    def _has_ancestor(self, cls: str) -> bool:
        return any(cls in classes for _, classes in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = set((attr_dict.get("class") or "").split())
        self.stack.append((tag, classes))

        if "ai-block" in classes:
            self._cur_ai_title = []
            self._cur_ai_content = []
        elif "ai-block-title" in classes:
            self._capture_target = "ai_title"
        elif "ai-block-content" in classes:
            self._capture_target = "ai_content"
        elif "word-group" in classes:
            self._cur_word_name = []
            self._cur_group_items = []
        elif "word-name" in classes:
            self._capture_target = "word_name"
        elif "standalone-group" in classes:
            self._cur_standalone_name = []
            self._cur_group_items = []
        elif "standalone-name" in classes:
            self._capture_target = "standalone_name"
        elif "news-item" in classes:
            self._cur_item = {"url": "", "title": "", "source": "", "rank": ""}
            self._cur_item_source = []
            self._cur_item_rank = []
            self._cur_item_title = []
        elif "source-name" in classes and self._cur_item is not None:
            self._capture_target = "item_source"
        elif "rank-num" in classes and self._cur_item is not None:
            self._capture_target = "item_rank"
        elif "news-title" in classes and self._cur_item is not None:
            self._capture_target = "item_title"
        elif tag == "a" and self._capture_target == "item_title" and self._cur_item is not None:
            href = (attr_dict.get("href") or "").strip()
            if href and not self._cur_item["url"]:
                self._cur_item["url"] = href
        elif tag == "br" and self._capture_target == "ai_content":
            self._cur_ai_content.append("<br/>")

    def handle_data(self, data: str) -> None:
        if not self._capture_target:
            return
        if self._capture_target == "ai_title":
            self._cur_ai_title.append(data)
        elif self._capture_target == "ai_content":
            self._cur_ai_content.append(html.escape(data))
        elif self._capture_target == "word_name":
            self._cur_word_name.append(data)
        elif self._capture_target == "standalone_name":
            self._cur_standalone_name.append(data)
        elif self._capture_target == "item_source":
            self._cur_item_source.append(data)
        elif self._capture_target == "item_rank":
            self._cur_item_rank.append(data)
        elif self._capture_target == "item_title":
            self._cur_item_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        _, classes = self.stack.pop()

        if "ai-block-title" in classes or "ai-block-content" in classes:
            self._capture_target = None
        elif "ai-block" in classes:
            title = "".join(self._cur_ai_title).strip()
            body = "".join(self._cur_ai_content).strip()
            if title and body:
                self.ai_blocks.append((title, body))
        elif "word-name" in classes or "standalone-name" in classes:
            self._capture_target = None
        elif "source-name" in classes or "rank-num" in classes or "news-title" in classes:
            self._capture_target = None
        elif "news-item" in classes and self._cur_item is not None:
            self._cur_item["title"] = "".join(self._cur_item_title).strip()
            self._cur_item["source"] = "".join(self._cur_item_source).strip()
            self._cur_item["rank"] = "".join(self._cur_item_rank).strip()
            if self._cur_item["title"]:
                self._cur_group_items.append(self._cur_item)
            self._cur_item = None
        elif "word-group" in classes:
            name = "".join(self._cur_word_name).strip()
            if name and self._cur_group_items:
                self.word_groups.append((name, list(self._cur_group_items)))
        elif "standalone-group" in classes:
            name = "".join(self._cur_standalone_name).strip()
            if name and self._cur_group_items:
                self.standalone_groups.append((name, list(self._cur_group_items)))


def _render_items_html(items: list[dict[str, str]], show_source: bool = True) -> str:
    lines = ["<ul>"]
    for item in items:
        title_esc = html.escape(item["title"])
        if item["url"]:
            link_html = f'<a href="{html.escape(item["url"])}">{title_esc}</a>'
        else:
            link_html = title_esc

        meta_parts: list[str] = []
        if show_source and item["source"]:
            meta_parts.append(html.escape(item["source"]))
        if item["rank"]:
            meta_parts.append(f"#{html.escape(item['rank'])}")
        meta_suffix = f" <small>({', '.join(meta_parts)})</small>" if meta_parts else ""
        lines.append(f"  <li>{link_html}{meta_suffix}</li>")
    lines.append("</ul>")
    return "\n".join(lines)


def build_entry_html(parser: TrendReportParser) -> str:
    """将提取的结构化数据拼装为适配 RSS 阅读器的精简 HTML 正文。"""
    parts: list[str] = []

    if parser.ai_blocks:
        parts.append("<h2>✨ AI 热点态势分析</h2>")
        for title, body in parser.ai_blocks:
            parts.append(f"<h3>{html.escape(title)}</h3>")
            parts.append(f"<p>{body}</p>")

    if parser.word_groups:
        parts.append("<h2>🔥 关注主题热榜</h2>")
        for group_name, items in parser.word_groups:
            parts.append(f"<h3>{html.escape(group_name)}（{len(items)}）</h3>")
            parts.append(_render_items_html(items, show_source=True))

    if parser.standalone_groups:
        parts.append("<h2>📊 核心平台榜单</h2>")
        for platform_name, items in parser.standalone_groups:
            parts.append(f"<h3>{html.escape(platform_name)}（{len(items)}）</h3>")
            parts.append(_render_items_html(items, show_source=False))

    if not parts:
        parts.append(f'<p>本次汇总无匹配条目，请查看 <a href="{SITE_BASE_URL}/">完整网页报告</a>。</p>')

    parts.append(f'<hr/><p><a href="{SITE_BASE_URL}/">查看完整交互网页版报告</a></p>')
    return "\n".join(parts)


def load_previous_entries(prev_feed_path: Path) -> list[ET.Element]:
    """从已有 feed.xml 加载历史条目以保持时间线连续。"""
    if not prev_feed_path.exists():
        return []
    try:
        tree = ET.parse(prev_feed_path)
        root = tree.getroot()
        return list(root.findall(f"{{{ATOM_NS}}}entry"))
    except ET.ParseError:
        return []


def create_entry_element(
    entry_id: str, title: str, updated_iso: str, link_href: str, html_body: str
) -> ET.Element:
    entry = ET.Element(f"{{{ATOM_NS}}}entry")
    ET.SubElement(entry, f"{{{ATOM_NS}}}title").text = title
    link = ET.SubElement(entry, f"{{{ATOM_NS}}}link")
    link.set("href", link_href)
    ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = entry_id
    ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = updated_iso
    content = ET.SubElement(entry, f"{{{ATOM_NS}}}content")
    content.set("type", "html")
    content.text = html_body
    return entry


def main() -> None:
    index_path = Path("index.html")
    if not index_path.exists():
        raise FileNotFoundError("index.html not found; ensure TrendRadar ran first.")

    dist_dir = Path("gh_pages_dist")
    dist_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(index_path, dist_dir / "index.html")

    raw_html = index_path.read_text(encoding="utf-8")
    parser = TrendReportParser()
    parser.feed(raw_html)
    body_html = build_entry_html(parser)

    now_cst = datetime.now(CST)
    updated_iso = now_cst.isoformat(timespec="seconds")
    run_stamp = now_cst.strftime("%Y%m%d-%H%M")
    entry_id = f"{SITE_BASE_URL}/#run-{run_stamp}"
    entry_title = f"TrendRadar 热点雷达 · {now_cst.strftime('%Y-%m-%d %H:%M')}"

    new_entry = create_entry_element(
        entry_id=entry_id,
        title=entry_title,
        updated_iso=updated_iso,
        link_href=f"{SITE_BASE_URL}/?t={run_stamp}",
        html_body=body_html,
    )

    prev_entries = load_previous_entries(Path("gh_pages_prev/feed.xml"))
    seen_ids = {entry_id}
    merged_entries = [new_entry]
    for item in prev_entries:
        item_id_el = item.find(f"{{{ATOM_NS}}}id")
        item_id = (item_id_el.text or "").strip() if item_id_el is not None else ""
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            merged_entries.append(item)
        if len(merged_entries) >= MAX_ENTRIES:
            break

    ET.register_namespace("", ATOM_NS)
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    ET.SubElement(feed, f"{{{ATOM_NS}}}title").text = "TrendRadar 全网热榜与 AI 分析"
    self_link = ET.SubElement(feed, f"{{{ATOM_NS}}}link")
    self_link.set("href", FEED_URL)
    self_link.set("rel", "self")
    alt_link = ET.SubElement(feed, f"{{{ATOM_NS}}}link")
    alt_link.set("href", f"{SITE_BASE_URL}/")
    ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = f"{SITE_BASE_URL}/"
    ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = updated_iso

    for entry in merged_entries:
        feed.append(entry)

    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")
    tree.write(dist_dir / "feed.xml", encoding="utf-8", xml_declaration=True)
    print(f"Generated feed.xml with {len(merged_entries)} entries (latest: {entry_title}).")


if __name__ == "__main__":
    main()

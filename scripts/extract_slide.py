"""
PPTXファイルから指定したスライドの内容を抽出するスクリプト。
スライドはインデックス（番号）またはタイトル文字列で指定できる。

Usage:
    python extract_slide.py <pptx_path> <slide_spec>

    slide_spec:
        数字の場合: スライド番号（1始まり）
        文字列の場合: スライドタイトルに含まれる文字列で検索
"""

import sys
import json
from pathlib import Path


def extract_slide(pptx_path: str, slide_spec: str) -> dict:
    try:
        from pptx import Presentation
    except ImportError:
        print("ERROR: python-pptx が必要です。pip install python-pptx を実行してください。")
        sys.exit(1)

    path = Path(pptx_path)
    if not path.exists():
        print(f"ERROR: ファイルが見つかりません: {pptx_path}")
        sys.exit(1)

    prs = Presentation(str(path))
    total_slides = len(prs.slides)

    target_slide = None
    target_index = None

    # スライド番号で指定（1始まり）
    if slide_spec.isdigit():
        idx = int(slide_spec) - 1
        if 0 <= idx < total_slides:
            target_slide = prs.slides[idx]
            target_index = idx + 1
        else:
            print(f"ERROR: スライド番号 {slide_spec} は範囲外です（全{total_slides}枚）")
            sys.exit(1)
    else:
        # タイトル文字列で検索
        for i, slide in enumerate(prs.slides):
            if slide.shapes.title and slide_spec in slide.shapes.title.text:
                target_slide = slide
                target_index = i + 1
                break
        if target_slide is None:
            print(f"ERROR: タイトルに '{slide_spec}' を含むスライドが見つかりません")
            sys.exit(1)

    # スライドのテキストを抽出
    title_text = ""
    if target_slide.shapes.title and target_slide.shapes.title.text.strip():
        title_text = target_slide.shapes.title.text.strip()

    body_texts = []
    for shape in target_slide.shapes:
        if not shape.has_text_frame:
            continue
        text = shape.text_frame.text.strip()
        if not text or text == title_text:
            continue
        # 段落ごとに分割して空行を除去
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            body_texts.append("\n".join(lines))

    return {
        "file": path.name,
        "slide_number": target_index,
        "total_slides": total_slides,
        "title": title_text or f"スライド {target_index}",
        "body": "\n\n".join(body_texts),
    }


def format_as_discussion_topic(slide: dict) -> str:
    lines = [
        f"## 📊 参照スライド情報",
        f"",
        f"- **ファイル**: {slide['file']}",
        f"- **スライド番号**: {slide['slide_number']} / {slide['total_slides']}",
        f"",
        f"## 📝 スライドタイトル",
        f"",
        slide["title"],
        f"",
        f"## 📄 スライド内容",
        f"",
        slide["body"] if slide["body"] else "（本文テキストなし）",
        f"",
        f"---",
        f"",
        f"> このIssueはスライド内容から自動生成されました。",
        f"> 上記テーマについてAIエージェントが多角的に議論します。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python extract_slide.py <pptx_path> <slide_spec>")
        sys.exit(1)

    pptx_path = sys.argv[1]
    slide_spec = sys.argv[2]

    slide = extract_slide(pptx_path, slide_spec)
    print(format_as_discussion_topic(slide))
    print(f"\n__SLIDE_TITLE__={slide['title']}", file=sys.stderr)

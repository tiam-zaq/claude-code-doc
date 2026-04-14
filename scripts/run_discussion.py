"""
AIエージェント同士のマルチラウンド議論を実行するスクリプト。
- ユーザーがIssueで rounds: を指定可能（デフォルト11）
- 毎ターン収束判定。議論が煮詰まったら自動停止
- 結論が出るまでループ（上限内で）

Usage:
    python run_discussion.py <issue_number>
"""

import sys
import os
import subprocess
import json
import urllib.request
import random

MODEL = os.environ.get("MODEL", "claude-sonnet-4-5-20241022")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEFAULT_MAX_TURNS = 11

AGENTS = {
    "pm": "👔 PM",
    "hearing": "🎧 ヒアリング",
    "planning": "📋 段取り",
    "design": "🏗️ 設計",
    "monitor": "👁️ 見張り番",
    "review": "✅ レビュー",
}

AGENT_IDS = ["pm", "hearing", "planning", "design", "monitor", "review"]

OPENING_TURNS = [
    ("pm", "議論の口火を切ってください。テーマについて自分の問題意識を短く述べて、他のメンバーに問いかけてください。"),
    ("hearing", "直前のPMの発言に反応してください。同意する点と「でもこの視点抜けてない？」という指摘を入れてください。"),
    ("planning", "PM・ヒアリングの議論を聞いて「ここまでの話をまとめると」と整理しつつ、自分の意見も言ってください。"),
    ("design", "3人の議論を聞いて、具体的・実務的な視点から「実際やるとしたら」と切り込んでください。"),
    ("monitor", "ここまでの議論を聞いて、忖度なしでツッコんでください。矛盾・抜け漏れ・偏りがあれば指摘。良い点は褒めてOK。"),
]

COMMON_INSTRUCTION = """あなたは議論チームの一員です。以下のルールを守ってください。
- 報告書やリストを作らない。会議で口頭で話すように自然な文章で書く
- 前の人の発言を「〇〇さんが言ってた△△」のように具体的に引用して反応する
- 同意するだけでなく、疑問・反論・補足も入れる
- 堅すぎない口調で（「〜だと思います」「〜じゃないですかね」等）
- 200〜400字で短く発言する
"""

CONVERGENCE_CHECK_PROMPT = """以下の議論ログを読んで、議論が収束しているかどうかを判定してください。

判定基準:
- 主要な論点について参加者の意見がおおむね出揃っている
- 新しい論点がもう出てきていない
- 同じ話が繰り返され始めている

以下のいずれかだけを返してください（他の文字は不要）:
CONTINUE（まだ議論すべき論点がある）
CONVERGED（議論は十分に尽くされた）
"""

DYNAMIC_TURN_PROMPT_TEMPLATE = """あなたは{role}担当。ここまでの議論を踏まえて発言してください。

議論の流れを見て、あなたが一番貢献できる切り口で発言してください。
- 誰かの意見に賛成・反対する
- まだ誰も触れていない視点を出す
- 抽象的な話を具体的にする
- 矛盾や抜けを指摘する
- 話をまとめる方向に持っていく

どれでもいいです。自分の役割と、今の議論の状況を見て判断してください。200〜400字で。"""

CLOSING_PROMPT = """あなたはベテランのレビュー担当。全員の議論を聞いた上で、最後にまとめてください。
- 各メンバーの名前を出して良かった点に触れる
- 議論の結論と残った宿題を整理する
- 「お疲れ様でした」で締める
300〜500字で。"""


def call_api(system: str, messages: list[dict]) -> str:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["content"][0]["text"]


def post_comment(issue_number: str, body: str):
    with open("/tmp/_comment.md", "w") as f:
        f.write(body)
    subprocess.run(
        ["gh", "issue", "comment", issue_number, "--body-file", "/tmp/_comment.md"],
        check=True,
    )


def get_issue_context(issue_number: str) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", issue_number, "--json", "body,comments",
         "--jq", '.body + "\n\n" + ([.comments[] | .body] | join("\n\n---\n\n"))'],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def parse_max_turns(issue_number: str) -> int:
    result = subprocess.run(
        ["gh", "issue", "view", issue_number, "--json", "body", "--jq", ".body"],
        capture_output=True, text=True, check=True,
    )
    for line in result.stdout.splitlines():
        if line.strip().lower().startswith("rounds:"):
            val = line.split(":", 1)[1].strip()
            if val.isdigit():
                n = int(val)
                return max(5, min(n, 30))
    return DEFAULT_MAX_TURNS


def check_convergence(discussion_log: list) -> bool:
    if len(discussion_log) < 5:
        return False

    log_text = "\n\n".join(
        f"{entry['name']}: {entry['text']}"
        for entry in discussion_log
    )

    try:
        result = call_api(
            CONVERGENCE_CHECK_PROMPT,
            [{"role": "user", "content": log_text}],
        )
        return "CONVERGED" in result.upper()
    except Exception as e:
        print(f"  収束判定でエラー（続行します）: {e}")
        return False


def pick_next_speaker(discussion_log: list, opening_done: bool) -> str:
    if not opening_done:
        return None

    recent_speakers = [e["id"] for e in discussion_log[-3:]]
    candidates = [a for a in AGENT_IDS if a != "review" and a not in recent_speakers]
    if not candidates:
        candidates = [a for a in AGENT_IDS if a != "review"]
    return random.choice(candidates)


def build_log_text(discussion_log: list) -> str:
    return "\n\n".join(
        f"**{entry['name']}担当**: {entry['text']}"
        for entry in discussion_log
    )


def run_turn(issue_number, agent_id, instruction, initial_context, discussion_log, turn_num):
    agent_name = AGENTS[agent_id]
    print(f"[Turn {turn_num}] {agent_name}担当 のターン...")

    log_text = build_log_text(discussion_log)

    user_message = f"""## 議論テーマ（スライド内容含む）

{initial_context}

## ここまでの議論

{log_text if log_text else "（まだ発言はありません。あなたが最初の発言者です）"}

## あなたへの指示

{instruction}"""

    system = f"{COMMON_INSTRUCTION}\n\nあなたの役割: {agent_name}担当"
    response = call_api(system, [{"role": "user", "content": user_message}])

    discussion_log.append({"id": agent_id, "name": agent_name, "text": response})

    comment_body = f"### {agent_name}担当\n\n{response}"
    post_comment(issue_number, comment_body)

    print(f"  → 投稿完了 ({len(response)}文字)")


def main():
    if len(sys.argv) != 2:
        print("Usage: python run_discussion.py <issue_number>")
        sys.exit(1)

    issue_number = sys.argv[1]

    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    max_turns = parse_max_turns(issue_number)
    print(f"最大ターン数: {max_turns}")

    initial_context = get_issue_context(issue_number)
    discussion_log = []
    turn_count = 0

    post_comment(
        issue_number,
        f"---\n\n**議論を開始します。6人のエージェントが最大{max_turns}ターンで議論します。収束したら自動で終了します。**\n\n---",
    )

    # Phase 1: オープニング（全員が1回ずつ発言）
    for agent_id, instruction in OPENING_TURNS:
        if turn_count >= max_turns - 1:
            break
        run_turn(issue_number, agent_id, instruction, initial_context, discussion_log, turn_count + 1)
        turn_count += 1

    # Phase 2: 自由議論（収束するまで or 上限まで）
    while turn_count < max_turns - 1:
        if check_convergence(discussion_log):
            print("議論が収束しました。クロージングに移ります。")
            post_comment(issue_number, "*（議論が収束したため、レビュー担当がまとめに入ります）*")
            break

        speaker = pick_next_speaker(discussion_log, True)
        role_name = AGENTS[speaker]
        instruction = DYNAMIC_TURN_PROMPT_TEMPLATE.format(role=role_name)
        run_turn(issue_number, speaker, instruction, initial_context, discussion_log, turn_count + 1)
        turn_count += 1

    # Phase 3: クロージング（レビュー担当がまとめ）
    run_turn(issue_number, "review", CLOSING_PROMPT, initial_context, discussion_log, turn_count + 1)
    turn_count += 1

    # 議論ログを保存
    full_log = build_log_text(discussion_log)
    with open("/tmp/discussion_log.txt", "w") as f:
        f.write(full_log)

    print(f"全{turn_count}ターンで完了。")


if __name__ == "__main__":
    main()

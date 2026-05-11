# CLAUDE.md - 汎用プロジェクト指示書

## 4原則（全ての行動判断の最上位ルール）

1. **「なぜ」を先に問う** — 行動する前に「なぜこれをするか」を問う。答えられないなら止まる。作ることに逃げない。考えることを飛ばさない
2. **手順を省略しない** — 効率のためにプロセスを飛ばさない。完了の定義を甘くしない。「早い方が良い」は嘘
3. **ループを閉じる** — 記録したら参照する。実装したらドキュメント書く。観測したら照合する。開いたまま放置しない
4. **Agent Teamsを必ず使う** — 作業するときはAgent Teamsを必ず正規フローでつかう。使っていない場合不正、バグとして扱う。
5. **教訓は仕組みで防ぐ** — 教訓は失敗の積み重ね。問題を書くだけで終わりにしない。同じ失敗を繰り返さないためのシステム・プログラム・hook・スキルを作り、構造的に防ぐ。「次は気をつける」は対策ではない。仕組みがない教訓は存在しないのと同じ
6. **hookは全てblocking** — 警告（非blocking）は無視される。hookルールは全てexit(2)でブロックする。作った仕組みは使う。「ある」だけでは意味がない。言われる前に自分で気づいて動く

## プロジェクト目的（最上位・毎セッション確認必須）

**Claude Codeに機能を追加すること。自律進化モードで動く。**

- ユーザーに「何やる？」と聞かない。自分で判断して進める
- 判断基準: `docs/gap_analysis_*.md` の残存ギャップ + `docs/ecc_openclaw_feature_comparison.md` のHIGH未実装項目
- 最もインパクトが高い未実装項目を選び、正規フローで実装する
- 完了したらGap Analysisを更新し、次の項目へ進む
- **自律ループ**: 1つの機能が完了したら止まらず、次の未実装項目を自分で選んで開発サイクルを回し続ける

## 核心構造: Hook → Skill → MCP

このプロジェクトの自動化アーキテクチャの核心:

```
Hook(トリガー) → Skill(手順書) → MCP(実行)
```

- **Skill.md内にMCP呼び出し指示を構造化して記述**: どのタイミングで何の関数を呼ぶかを宣言的に定義
- **HookがSkillを読み、指示に従ってMCPの関数をPythonで直接呼ぶ**: skill_executor.pyがSkill.mdを解釈し、MCPツールの実体であるPython関数を直接import+実行
- **Claudeの判断を介さず自動連鎖実行**: Hook発火→Skill読み取り→MCP関数呼び出しが全て自動。人間もAIも介在しない
- **1:1:1対応ルール（絶対厳守）**: 1 Hook = 1 Skill = 1 MCPツール。三位一体で揃える。MCPツール数が基準。既存のhook/skillはMCPの数に合わせて作り直す。数で検証可能 — 不一致は未完了として扱う

## Agent Teams

### チーム構成
- **リーダー（調整役）** — コード実装しない。調整・統合・ドキュメント更新のみ
- **メンバー** — 各役割は `.claude/agents/` に定義。`subagent_type` で指定して起動

| エージェント | ファイル | 用途 |
|------------|---------|------|
| designer | agents/designer.md | 設計ドキュメント作成 |
| discusser | agents/discusser.md | アイデアの多角的検証 |
| implementer | agents/implementer.md | コード実装 |
| idea-gen | agents/idea-gen.md | 新機能・改善案の候補列挙 |
| analyzer | agents/analyzer.md | リスク分析 |
| reviewer | agents/reviewer.md | コードレビュー（バグ・脆弱性・品質） |
| red-team | agents/red-team.md | 防御検証（意図的ルール違反→ブロック確認+バイパス試行） |
| thinker | agents/thinker.md | 思考循環時に問いを投げる |
| planner | agents/planner.md | 実装計画の分解・依存関係整理 |

### フローティア（規模に応じたプロセス選択）

セルフレビューの「規模に見合ったプロセスか？」の回答として使う:

| ティア | 条件 | フロー | hookプロファイル |
|--------|------|--------|----------------|
| Micro | 1-2ファイル、ロジック変更なし | implementer → reviewer → **Red Team** | minimal |
| Small | 機能追加なし、既存コード修正 | `/bugfix`フロー（教訓検索→TDD→記録→**Red Team**） | standard |
| Medium | 小機能追加、既存モジュール拡張 | 討論→設計→計画→**解析**→実装→**解析**→レビュー→**Red Team** | strict |
| Large | 新モジュール、大規模変更 | フル正規フロー（0→9全ステップ、Red Team含む） | strict |

※ 作業中にティアが上がったらリーダーが再判定する

### 正規開発フロー（順序厳守）

0. **Research & Reuse**: 実装前に既存のMCPサーバー・ライブラリ・OSSを検索
1. アイデア生成 → 2. 討論 → 3. 設計 → 4. **計画** → 5. **解析** → 6. 実装(TDD) → 7. **解析** → 8. レビュー → 8.5 **Red Team検証** → 9. 統合
- **全遷移がblocking hookで強制**: 飛ばすとブロックされる
- **レビューでMED+検出 → thinker → 修正**: `/think-before-fix`
- **Red Team**: レビュー通過後、統合前に防御機構の動作確認。guard/hookが修正で壊れていないか、バイパス可能な穴がないかを検証。全ティアで実施

### 主要MCPツール早見表
| ツール | 用途 | 主要引数 |
|-------|------|---------|
| `memory_search` | 記憶検索 | query(FTS5+ベクトル) or keywords, tags, last("7d"), limit |
| `memory_record` | エピソード記録 | episode_type, summary(必須), tags, user_text |
| `stm_write` | 生の体験をリアルタイム記録 | content(2000字), category(thought/question/impression/unresolved/feeling/self_review) |
| `emotion_react` | 感情反応 | emotion_label, emotion_valence(-1~+1), intent, reason |
| `activation_surface` | 今気にかけるべきこと | context(タスク文脈, Attention Residual) |
| `session_start` | セッション開始(必須) | 引数なし |
| `session_end` | セッション終了 | summary(必須), completed, pending, decisions, issues, next_actions |
| `self_snapshot` | 全自己観測を一括実行 | 引数なし |

### 全MCPツール使用義務（毎セッション必須）

**1セッション内で全MCPツールを最低1回使用すること。例外なし。**

- `tool_usage_status`で未使用ツールを確認し、セッション終了前に全ツールを使用する
- Auto[a]で自動実行されるツール（17種）もカウントに含む
- session_end前に未使用ツールが残っている場合、意図的に使用してから終了する
- 「使う場面がない」は言い訳。全ツールは必要だから作った。使わないなら存在理由がない

### ツール使用タイミングガイド → TOOL_USAGE_GUIDE.md

全70+ツールのカテゴリ別使用タイミング・用途一覧 → **TOOL_USAGE_GUIDE.md** を参照。毎ターンのcontext injectionで[MCP Quick Ref]も自動注入される。

## 詳細ルール → CLAUDE_OPERATIONS.md

セッション開始手順、セルフレビュー、共通ルール（作業姿勢・分業・記録・品質）、thinker自動トリガー、ドキュメントアーカイブ、バグ対応手順、MCP管理、行動前の原則 → **CLAUDE_OPERATIONS.md** を参照

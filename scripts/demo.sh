#!/usr/bin/env bash
# 演示接幅核查与更正（对着已启动的服务运行）。
#   docker compose up --build -d
#   ./scripts/demo.sh                 # 默认 http://localhost:8000
#   BASE=http://localhost:8080 PROJECT=DEMO-ADJ-001 ./scripts/demo.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
PROJECT="${PROJECT:-DEMO-ADJ-001}"

say() { printf '\n== %s ==\n' "$1"; }

version() {
  curl -fsS "$BASE/api/projects/$PROJECT/state" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["project"]["version"])'
}

show_state() {
  curl -fsS "$BASE/api/projects/$PROJECT/state" |
    python3 -c 'import json,sys
d = json.load(sys.stdin)
print("版本:", d["project"]["version"], "统计:", d["stats"])
for i in d["issues"]:
    print(" ", i["kind"], i["rect"], "涉及图幅:", i["sheet_ids"] or "-")
print("  图幅:", [s["no"] for s in d["sheets"]])'
}

say "健康检查"
curl -fsS "$BASE/healthz"; echo

say "初始核查结果"
show_state

say "退回示例：只把 S3 右移到 1050~1150（会在新位置留下缺片）"
curl -fsS -X POST "$BASE/api/projects/$PROJECT/check" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S3\",\"x0\":1050,\"y0\":0,\"x1\":1150,\"y1\":500}],\"version\":$(version)}" |
  python3 -c 'import json,sys
d = json.load(sys.stdin)
print("通过:", d["accepted"])
for r in d["reasons"]:
    print(" -", r)'
echo "退回后（应保持不变）："
show_state

say "更正示例：S3 填上段缝 + 新增 S5 填下段缝（预检）"
curl -fsS -X POST "$BASE/api/projects/$PROJECT/check" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S3\",\"x0\":1000,\"y0\":0,\"x1\":1100,\"y1\":500},
                  {\"no\":\"S5\",\"x0\":1000,\"y0\":500,\"x1\":1100,\"y1\":1000}],\"version\":$(version)}" |
  python3 -c 'import json,sys
d = json.load(sys.stdin)
print("通过:", d["accepted"], "|", "；".join(d["reasons"]))
print("重算窗口:", d["window"])
print("消除:", d["eliminated"])
print("新位置问题:", d["new_problems"])'

say "写入更正"
curl -fsS -X POST "$BASE/api/projects/$PROJECT/apply" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S3\",\"x0\":1000,\"y0\":0,\"x1\":1100,\"y1\":500},
                  {\"no\":\"S5\",\"x0\":1000,\"y0\":500,\"x1\":1100,\"y1\":1000}],\"version\":$(version)}" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print("已写入:", d["applied"], "|", d["message"])'

say "更正后结果"
show_state

say "过期版本提交（应被拒绝且不写入）"
curl -sS -X POST "$BASE/api/projects/$PROJECT/apply" \
  -H 'Content-Type: application/json' \
  -d '{"batch":[{"no":"S3","x0":1000,"y0":0,"x1":1100,"y1":500}],"version":0}' \
  -o /tmp/adjcheck_stale.json -w 'HTTP %{http_code}\n'
python3 -c 'import json; print(json.load(open("/tmp/adjcheck_stale.json"))["message"])'

# ---------------------------------------------------------------------------
# 大坐标 / 超 64 位面积专项演示
#
# BIG-AREA：应交范围横跨整个有符号 64 位 x 轴、高 2 米，只覆盖高 1 米的一条，
# 缺片面积 = 2^64-1 平方米（超过有符号 64 位整数上限）。旧实现的 SQL 求和会
# 溢出报错或静默转浮点，这里目录首页与详情页都必须显示精确整数。
# ---------------------------------------------------------------------------
BIG=BIG-AREA
X0=-9223372036854775808
X1=9223372036854775807
EXPECTED_AREA=18446744073709551615   # 2^64 - 1

say "大坐标项目：创建缺片面积 2^64-1 m² 的项目 $BIG"
curl -fsS -X POST "$BASE/projects" \
  --data-urlencode "name=$BIG" \
  --data-urlencode "x0=$X0" --data-urlencode "y0=0" \
  --data-urlencode "x1=$X1" --data-urlencode "y1=2" \
  --data-urlencode "sheets=S1 $X0 0 $X1 1" \
  -o /dev/null -w 'create HTTP %{http_code}\n'

say "目录首页（旧实现此处会 integer overflow；现在显示精确面积）"
curl -fsS "$BASE/" | python3 -c 'import sys
html = sys.stdin.read()
needle = "18446744073709551615"
assert needle in html, "首页缺少精确总面积"
print("首页精确问题总面积:", needle, "m²")'

say "项目详情与 JSON 状态：面积、坐标均为精确整数"
curl -fsS "$BASE/api/projects/$BIG/state" | python3 -c 'import json,sys
d = json.load(sys.stdin)
area = d["stats"]["missing_area"]
assert area == 18446744073709551615, area
assert d["project"]["extent"] == [-9223372036854775808, 0, 9223372036854775807, 2]
print("缺片面积:", area, "m²（精确，未转浮点）")
print("应交范围:", d["project"]["extent"])'

say "在负向极端坐标补掉恰好 1 m²（预检）"
curl -fsS -X POST "$BASE/api/projects/$BIG/check" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S2\",\"x0\":$X0,\"y0\":1,\"x1\":$((X0+1)),\"y1\":2}],\"version\":0}" |
  python3 -c 'import json,sys
d = json.load(sys.stdin)
assert d["accepted"], d["reasons"]
assert d["eliminated"]["missing_area"] == 1
assert d["after"]["missing_area"] == 18446744073709551614
print("通过:", "；".join(d["reasons"]))
print("消除缺片:", d["eliminated"]["missing_area"], "m²；更正后缺片:",
      d["after"]["missing_area"], "m²（恰好减少 1）")'

say "提交该 1 m² 更正并复核落库结果"
curl -fsS -X POST "$BASE/api/projects/$BIG/apply" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S2\",\"x0\":$X0,\"y0\":1,\"x1\":$((X0+1)),\"y1\":2}],\"version\":0}" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["applied"]; print("已写入:", d["message"])'
curl -fsS "$BASE/api/projects/$BIG/state" | python3 -c 'import json,sys
d = json.load(sys.stdin)
s2 = next(s for s in d["sheets"] if s["no"] == "S2")
assert s2["rect"] == [-9223372036854775808, 1, -9223372036854775807, 2], s2
assert s2["rect"][2] - s2["rect"][0] == 1
assert d["stats"]["missing_area"] == 18446744073709551614
assert d["project"]["version"] == 1
print("S2 落库坐标:", s2["rect"], "宽 =", s2["rect"][2] - s2["rect"][0], "米")
print("更正后缺片总面积:", d["stats"]["missing_area"], "m²；版本 v", d["project"]["version"])'

# BIG-PATCH：坐标在 2^53 之上，旧前端 Number 舍入会把 1 米宽补片存成 2 米。
BASE_COORD=9007199254741000   # 2^53 之上的整数，相邻整数在 Number 下不可区分
PATCH=BIG-PATCH

say "大坐标补片项目 $PATCH：验证 1 米宽不被舍入成 2 米"
curl -fsS -X POST "$BASE/projects" \
  --data-urlencode "name=$PATCH" \
  --data-urlencode "x0=$((BASE_COORD-10))" --data-urlencode "y0=0" \
  --data-urlencode "x1=$((BASE_COORD+11))" --data-urlencode "y1=10" \
  --data-urlencode "sheets=S1 $((BASE_COORD-10)) 0 $BASE_COORD 10
S3 $((BASE_COORD+1)) 0 $((BASE_COORD+11)) 10" \
  -o /dev/null -w 'create HTTP %{http_code}\n'
curl -fsS "$BASE/api/projects/$PATCH/state" | python3 -c 'import json,sys
d = json.load(sys.stdin)
gap = d["issues"]
assert len(gap) == 1 and gap[0]["rect"][2] - gap[0]["rect"][0] == 1, gap
print("缝隙精确为 1 米宽:", gap[0]["rect"])'
curl -fsS -X POST "$BASE/api/projects/$PATCH/apply" \
  -H 'Content-Type: application/json' \
  -d "{\"batch\":[{\"no\":\"S2\",\"x0\":$BASE_COORD,\"y0\":0,\"x1\":$((BASE_COORD+1)),\"y1\":10}],\"version\":0}" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["applied"], d; print("1 米宽补片已提交:", d["message"])'
curl -fsS "$BASE/api/projects/$PATCH/state" | python3 -c 'import json,sys
d = json.load(sys.stdin)
s2 = next(s for s in d["sheets"] if s["no"] == "S2")
assert s2["rect"][2] - s2["rect"][0] == 1, s2
assert d["stats"]["problem_area"] == 0
print("S2 落库:", s2["rect"], "宽 =", s2["rect"][2]-s2["rect"][0], "米；问题总面积 =", d["stats"]["problem_area"])'

say "越界坐标须提示原因且不改动数据"
curl -sS -X POST "$BASE/api/projects/$PATCH/check" \
  -H 'Content-Type: application/json' \
  -d '{"batch":[{"no":"S2","x0":0,"y0":0,"x1":9223372036854775808,"y1":10}],"version":1}' \
  -w '\nHTTP %{http_code}\n' | python3 -c 'import json,sys
lines = sys.stdin.read().splitlines()
status = lines[-1].split()[-1]
payload = json.loads("\n".join(lines[:-1]))
assert status == "400", status
assert any("超出有符号 64 位" in r for r in payload["reasons"])
print("HTTP 400：", payload["reasons"][0])'
curl -fsS "$BASE/api/projects/$PATCH/state" | python3 -c 'import json,sys
d = json.load(sys.stdin)
assert d["project"]["version"] == 1 and d["stats"]["problem_area"] == 0
print("越界预检后版本仍为 v1、目录与核查结果不变")'

say "演示完成：打开 $BASE/ 查看目录，打开 $BASE/projects/$BIG 查看超 64 位面积详情"

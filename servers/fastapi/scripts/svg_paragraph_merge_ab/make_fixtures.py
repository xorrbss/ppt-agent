"""Author-style svg fixtures (explicit x/y on every tspan, like SVG_AUTHOR_RULES asks)."""
import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
F = 'font-family="Noto Sans KR"'


def para(x, y, size, pitch, lines, fill="#1E293B", weight="normal", anchor=None):
    a = f' text-anchor="{anchor}"' if anchor else ""
    spans = "".join(f'<tspan x="{x}" y="{y + i * pitch}">{text}</tspan>' for i, text in enumerate(lines))
    return f'<text x="{x}" y="{y}" {F} font-size="{size}" font-weight="{weight}" fill="{fill}"{a}>{spans}</text>'


def svg(body, w=1188, h=840):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="#F8FAFC"/>{body}</svg>')


s1 = (para(70, 110, 38, 52, ["디지털 전환 로드맵과", "단계별 실행 전략"], "#0F172A", "bold")
      + para(70, 260, 18, 28, ["공장 현장의 설비 데이터를 하나의 표준 형식으로 수집하고,",
                               "수집된 데이터를 기반으로 품질 이상을 조기에 감지하는",
                               "체계를 올해 3분기까지 구축합니다."])
      + para(70, 420, 18, 28, ["첫 번째 문단은 현황을 설명합니다. 설비별 신호가 제각각이라 비교가 어렵습니다."])
      + para(70, 500, 16, 24, ["1단계에서는 파일럿 라인 두 곳에 센서를 설치합니다.",
                               "2단계에서는 전사 확산을 위한 표준 인터페이스를 확정합니다."])
      + para(70, 620, 16, 24, ["두 번째 문단과의 간격이 있는 블록입니다."], "#475569")
      + para(70, 700, 16, 24, ["세 줄짜리 본문입니다. 줄바꿈은 작성자가 정했습니다.",
                               "두 번째 줄은 첫 줄과 같은 크기로 이어집니다.", "마지막 줄입니다."], "#475569"))
s2 = (para(594, 120, 34, 46, ["가운데 정렬 제목", "두 줄로 구성됩니다"], "#0F172A", "bold", "middle")
      + para(594, 280, 18, 28, ["가운데 정렬된 설명 문단입니다.", "각 줄의 길이가 서로 다릅니다.", "짧은 줄"], anchor="middle")
      + para(1118, 440, 18, 28, ["오른쪽 정렬 주석 첫 줄", "오른쪽 정렬 주석 둘째 줄입니다"], "#64748B", anchor="end")
      + para(1118, 540, 14, 20, ["출처: 내부 자료(2026)", "단위: 억원"], "#64748B", anchor="end")
      + para(70, 640, 16, 24, ["English paragraph with a wrapped second line that is",
                               "still part of the same paragraph for reflow."], "#334155"))
s3 = (para(70, 110, 30, 40, ["핵심 과제"], "#0F172A", "bold")
      + para(70, 200, 18, 30, ["• 설비 데이터 표준화 및 수집 체계 구축", "• 품질 이상 조기 감지 모델 도입",
                               "• 현장 인력 교육과 운영 매뉴얼 정비", "• 보안 및 접근 권한 관리 체계 수립"])
      + para(70, 400, 18, 30, ["- 하이픈 목록 첫 항목입니다", "- 하이픈 목록 둘째 항목입니다", "- 하이픈 목록 셋째 항목입니다"])
      + para(70, 560, 16, 24, ["1. 번호 목록 첫 항목", "2. 번호 목록 둘째 항목", "3. 번호 목록 셋째 항목"])
      + '<rect x="580" y="170" width="520" height="120" fill="none" stroke="#CBD5E1" stroke-width="1"/>'
      + para(600, 200, 17, 26, ["카드 안의 두 줄 본문", "작은 카드 설명입니다"], "#334155")
      + para(600, 380, 17, 26, ["굵은 첫 줄 이후", "일반 문장으로 이어집니다"], "#334155", "bold"))
for i, s in enumerate((s1, s2, s3), 1):
    (out / f"ab-{i}.svg").write_text(svg(s), encoding="utf-8")

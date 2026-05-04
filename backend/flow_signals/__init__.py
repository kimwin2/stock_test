"""flow_signals: 퀀트 트레이더 스타일 수급/주도 분석 파이프라인.

핵심 출력:
- 시장 심리 (Fear & Greed 오실레이터, 업종 쏠림 지수)
- 주도 업종 (ETF Mansfield RS + 변동성 조정 모멘텀)
- 수급 빈집 (외국인+기관 누적 매수액의 5일 평균 vs 20일 평균 차이)
- 거래대금 강도 (종목별 7일 누적 z-score)
- 신고가 (50일/250일)
"""

from .pipeline import build_flow_dashboard

__all__ = ["build_flow_dashboard"]

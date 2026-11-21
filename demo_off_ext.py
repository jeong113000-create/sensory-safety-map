"""
발표 시연 뒤: 차담(kakao-1795099660, 보고서 카페 C)·카페쿠피(kakao-92860343, 카페 D)를
시연 가정값 대신 학습 서버 예측(추정 색, level_source "estimated")으로 보여줌.
설치: app_api.py 맨 끝에 `import demo_off_ext` 한 줄.  되돌리기(시연값 다시): 그 한 줄만 지우고 재시작.
"""
import demo_ext as DX

DX.DEMO.clear()
DX._bands.clear()

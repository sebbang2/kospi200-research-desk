# KOSPI200 대시보드 GitHub 저장소 설치

이 프로젝트는 비공개 GitHub 저장소와 GitHub Actions를 이용해 데이터 수집을 자동화합니다. 개인 계정의 비공개 저장소에서 GitHub Pages를 켜면 사이트 데이터가 공개될 수 있으므로, 현재 workflow에는 Pages 배포를 넣지 않았습니다.

1. GitHub에서 새 저장소를 만듭니다.
2. 이 폴더의 파일을 저장소 루트에 업로드합니다. `.github/workflows/refresh-and-deploy.yml`도 포함해야 합니다.
3. 저장소의 **Settings → Actions → General**에서 Workflow permissions를 **Read and write permissions**로 설정합니다.
4. **Actions → Refresh KOSPI200 dashboard → Run workflow**에서 `all`을 한 번 실행합니다.
5. 이후 데이터 파일은 비공개 저장소 안에서 자동 갱신됩니다.

예약 실행은 GitHub Actions가 담당합니다.

- 매일 12:00 KST: 뉴스
- 매일 15:40 KST: 뉴스
- 매주 월요일 12:05 KST: 가치평가
- 매월 1일 12:10 KST: 가치평가

GitHub Actions의 예약 작업은 장시간 지연될 수 있으므로 12:00·15:40에 정확히 실행된다고 보장되지는 않습니다. 실행 결과는 `valuation_data.json`, `news_data.json`에 커밋됩니다.

웹사이트를 본인만 보게 하려면 GitHub Pages가 아닌 로그인·접근제어가 가능한 별도 호스팅을 연결해야 합니다. GitHub 저장소의 비공개 여부와 Pages 사이트의 접근 제한은 별개입니다.

현재 공개 Naver 페이지 기반 수집은 로그인 없이 동작합니다. 기업별 주요 사업 키워드는 Naver 종목 페이지의 기업개요에서 최대 3개를 추출해 기업명 아래에 표시합니다. 기초 데이터가 비어 있는 첫 배포 직후에는 `사업정보 수집 전`으로 보이며, 첫 `all` 또는 `valuation` 실행 이후 채워집니다.


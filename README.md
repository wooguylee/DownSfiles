# DownSfiles

Suno 워크스페이스에서 수집한 곡 목록을 읽어 Python의 직접 HTTP 요청으로
오디오를 저장하는 도구입니다. 곡 생성, 브라우저 다운로드, 스피커 재생은 하지 않습니다.
목록 수집은 별도로 진행하며, 접근 권한이 있는 곡 목록을 사용합니다.

## 설치

Python 3.11 이상이 필요합니다. 프로젝트 폴더의 PowerShell에서 실행합니다.

```powershell
python -m venv downloads\.runtime
.\downloads\.runtime\Scripts\python.exe -m pip install -r requirements.txt
```

## 목록 준비

`downloads\manifests\workspace_example.txt`처럼 `downloads` 안에 UTF-8 텍스트
파일을 만듭니다. 실제 워크스페이스 ID와 전체 곡 수를 기록하고 각 행에는
`제목|곡ID`를 적습니다. 같은 제목의 후보는 `제목|곡ID1|곡ID2`로 적을 수도 있습니다.

```text
# workspace_id=<실제 워크스페이스 ID>
# expected_song_count=2
첫 번째 곡|00000000-0000-4000-8000-000000000001
두 번째 곡|00000000-0000-4000-8000-000000000002
```

위 ID는 형식 설명용 가상 값입니다. 실제 곡 ID로 바꿔 사용합니다.
전체 목록의 고유 ID 개수는 `expected_song_count`와 같아야 하며 중복 ID는 거부합니다.
목록에 `|`가 들어가는 제목은 다른 문자로 치환합니다.

## 다운로드

```powershell
.\download_workspace.ps1 --manifest .\downloads\manifests\workspace_example.txt
```

기본 출력 위치는 `downloads\example`입니다. 목록 파일명에서 `workspace_`를
제거한 이름을 사용합니다. 다른 출력 폴더와 동시 처리 수를 지정할 수도 있습니다.

```powershell
.\download_workspace.ps1 --manifest .\downloads\manifests\workspace_example.txt --output .\downloads\example --workers 3
```

- `--manifest`는 필수이며 목록과 출력 경로 모두 이 프로젝트의 `downloads` 안이어야 합니다.
- `--workers`는 1~6, 기본값은 3입니다. `--limit N`은 앞의 N곡만 처리합니다.
- 파일명은 `제목_전체곡ID`이며 제공되는 실제 형식에 따라 `.m4a`, `.webm`, `.mp3`를 사용합니다.
- 곡별 검증 정보는 출력 폴더의 `_metadata`, 진행 및 최종 결과는 `download_report.json`에 저장합니다.
- HTTP 응답 길이, 제공되는 경우 ETag 체크섬, 전체 오디오 디코딩, 파일 SHA-256을 검증합니다.
- 재실행 시 기존 파일의 크기와 SHA-256을 확인해 건너뜁니다. 검증 실패 파일은 덮어쓰지 않습니다.
- 일시적인 네트워크 오류는 최대 3회 시도하고, 실패한 곡은 보고서에 기록합니다. 실패가 있으면 종료 코드는 1입니다.

서버가 발급한 익명 재생 권한만 사용합니다. 권한이 거절되면 실패로 기록합니다.
인증 토큰, 쿠키, 복호화 키를 파일이나 로그에 저장하지 않습니다.

## 로컬 파일과 Git

개인 워크스페이스 목록, 오디오, 보고서, 임시 파일과 Python 실행 환경은 모두
`downloads` 아래에 둡니다. 이 폴더 전체는 Git에서 제외됩니다.
저장소에는 Python 코드 2개, PowerShell 실행 파일, 의존성 목록, 이 안내와
`.gitignore`만 포함합니다.

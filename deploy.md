# 🚀 Stock Dashboard 배포 가이드 (Step-by-Step)

이 문서는 Stock Dashboard 프로젝트를 **처음부터 AWS + GitHub Pages에 배포**하는 전체 과정을 안내합니다.

---

## 📋 사전 준비물

| 항목 | 상태 확인 |
|------|----------|
| AWS 계정 | ✅ |
| GitHub 계정 | ✅ |
| OpenAI API Key | `sk-...` 준비 |
| AWS CLI 설치 | 아래 참고 |
| SAM CLI 설치 | 아래 참고 |

---

## Step 1: 도구 설치

### AWS CLI 설치 (Windows)

```powershell
# winget으로 설치
winget install Amazon.AWSCLI

# 또는 직접 다운로드
# https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
```

### SAM CLI 설치 (Windows)

```powershell
# winget으로 설치  
winget install Amazon.SAM-CLI

# 설치 확인
sam --version
```

---

## Step 2: AWS IAM 사용자 생성

1. [AWS Console](https://console.aws.amazon.com/) 로그인
2. **IAM** 서비스 이동
3. **사용자** → **사용자 생성** 클릭
4. 사용자 이름: `stock-deployer`
5. **직접 정책 연결** 선택 후 아래 정책 추가:
   - `AmazonS3FullAccess`
   - `AWSLambda_FullAccess`
   - `AmazonEventBridgeFullAccess`
   - `IAMFullAccess`
   - `AWSCloudFormationFullAccess`
   - `AmazonAPIGatewayAdministrator` (선택)
6. **액세스 키 생성** (CLI 용도)
7. `Access Key ID`와 `Secret Access Key` 저장 (한 번만 표시됨!)

### AWS CLI 설정

```powershell
aws configure
# AWS Access Key ID: (위에서 생성한 키)
# AWS Secret Access Key: (위에서 생성한 시크릿)
# Default region name: ap-northeast-2
# Default output format: json
```

---

## Step 3: GitHub 리포지토리 Secrets 등록

1. GitHub 리포지토리 https://github.com/kimwin2/stock_test 접속
2. **Settings** 탭 클릭
3. **Secrets and variables** → **Actions** 클릭
4. **New repository secret** 버튼으로 아래 3개 등록:

| Secret Name | 값 |
|-------------|-----|
| `AWS_ACCESS_KEY_ID` | IAM 액세스 키 |
| `AWS_SECRET_ACCESS_KEY` | IAM 시크릿 키 |
| `OPENAI_API_KEY` | `sk-...` OpenAI 키 |

---

## Step 4: AWS 인프라 배포 (첫 배포)

### 4-1. 로컬에서 SAM 배포

```powershell
cd c:\Users\ymkim\.gemini\antigravity\scratch\market\front-end

# SAM 빌드 (Lambda 패키지 생성)
sam build

# 첫 배포 (대화형)
sam deploy --guided
```

대화형 배포 시 입력값:

```
Stack Name: stock-dashboard-stack
AWS Region: ap-northeast-2
Parameter OpenAIApiKey: sk-your-api-key-here
Parameter S3BucketName: stock-dashboard-data
Confirm changes before deploy: N
Allow SAM CLI IAM role creation: Y
Save arguments to configuration file: Y
```

### 4-2. 또는 한 줄로 배포

```powershell
sam build; sam deploy --parameter-overrides "OpenAIApiKey=sk-your-api-key"
```

### 4-3. 배포 확인

```powershell
# Lambda 함수 확인
aws lambda get-function --function-name stock-pipeline --region ap-northeast-2

# S3 버킷 확인
aws s3 ls s3://stock-dashboard-data/

# Lambda 수동 실행 테스트
aws lambda invoke --function-name stock-pipeline --region ap-northeast-2 output.json
cat output.json
```

---

## Step 5: GitHub Pages 활성화

1. GitHub 리포지토리 → **Settings** → **Pages**
2. **Source** 드롭다운에서 **GitHub Actions** 선택
3. 저장

이제 `frontend/` 폴더 변경 시 자동으로 GitHub Pages 배포됩니다.

### 배포 후 접속 URL

```
https://kimwin2.github.io/stock_test/
```

---

## Step 6: Git Push로 자동 배포

```powershell
cd c:\Users\ymkim\.gemini\antigravity\scratch\market\front-end

# Git 초기화 (이미 되어있으면 스킵)
git init
git remote add origin https://github.com/kimwin2/stock_test.git

# 전체 파일 커밋 및 푸시
git add .
git commit -m "🚀 Stock Dashboard: AWS Lambda + GitHub Pages 배포 파이프라인"
git branch -M main
git push -u origin main
```

Push 후 GitHub Actions 탭에서 두 개의 워크플로우가 실행됩니다:
1. **Deploy Lambda Backend** → AWS Lambda 배포
2. **Deploy Frontend to GitHub Pages** → 웹사이트 배포

---

## 🔍 트러블슈팅

### Lambda 타임아웃

Lambda 기본 타임아웃이 3초입니다. template.yaml에서 300초(5분)로 설정되어 있지만, 
크롤링이 느린 경우 CloudWatch Logs에서 확인하세요.

```powershell
# CloudWatch 로그 확인
aws logs tail /aws/lambda/stock-pipeline --follow --region ap-northeast-2
```

### S3 CORS 오류

브라우저 콘솔에 CORS 에러가 나면:

```powershell
# S3 CORS 수동 설정
aws s3api put-bucket-cors --bucket stock-dashboard-data --cors-configuration '{
  "CORSRules": [{
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET"],
    "AllowedOrigins": ["*"],
    "MaxAgeSeconds": 3600
  }]
}'
```

### S3 퍼블릭 액세스 확인

```powershell
# 퍼블릭 접근 테스트
curl https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/dashboard_data.json
```

### GitHub Pages 404

- Settings → Pages에서 Source가 **GitHub Actions**인지 확인
- `frontend/index.html` 파일이 존재하는지 확인
- Actions 탭에서 배포 워크플로우 로그 확인

---

## 📊 모니터링

### Lambda 실행 확인

```powershell
# 최근 실행 로그
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/stock --region ap-northeast-2

# EventBridge 스케줄 확인
aws events list-rules --name-prefix stock --region ap-northeast-2
```

### S3 데이터 갱신 확인

```powershell
# 마지막 업데이트 시간 확인
aws s3api head-object --bucket stock-dashboard-data --key dashboard_data.json --region ap-northeast-2
```

---

## ⚠️ 비용 관리

### EventBridge 스케줄 일시 중지/재개

```powershell
# 스케줄 중지 (비용 절약)
aws events disable-rule --name stock-pipeline-schedule --region ap-northeast-2

# 스케줄 재개
aws events enable-rule --name stock-pipeline-schedule --region ap-northeast-2
```

### 전체 인프라 삭제

```powershell
# S3 버킷 비우기 (삭제 전 필수)
aws s3 rm s3://stock-dashboard-data --recursive

# CloudFormation 스택 삭제 (모든 리소스 삭제)
aws cloudformation delete-stack --stack-name stock-dashboard-stack --region ap-northeast-2
```

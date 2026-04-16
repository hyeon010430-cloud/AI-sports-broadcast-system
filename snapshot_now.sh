#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d-%H%M%S)"
SNAP="$HOME/system_snapshot_$TS"
mkdir -p "$SNAP"

echo "==> Snapshot dir: $SNAP"

# 0) 기본 정보
{
  echo "==== uname ===="; uname -a
  echo "==== lsb_release ===="; command -v lsb_release && lsb_release -a || true
  echo "==== kernel modules (gpu hint) ===="; lsmod | head -n 50
  echo "==== env ===="; env | sort
} > "$SNAP/system_info.txt" 2>&1 || true

# 1) 패키지/드라이버/런타임 버전
mkdir -p "$SNAP/versions"
{ python3 -V; pip -V; pip list --format=freeze || true; } > "$SNAP/versions/python.txt" 2>&1 || true
{ node -v; npm -v; npm list --depth=0 || true; } > "$SNAP/versions/node.txt" 2>&1 || true
{ ffmpeg -hide_banner -version || true; } > "$SNAP/versions/ffmpeg.txt" 2>&1 || true
{ nvidia-smi -q || true; } > "$SNAP/versions/nvidia_smi.txt" 2>&1 || true
{ nvcc -V || true; } > "$SNAP/versions/cuda.txt" 2>&1 || true

# 2) APT/SNAP 패키지 목록(우분투)
mkdir -p "$SNAP/packages"
{ apt-mark showmanual || true; } > "$SNAP/packages/apt_manual.txt" 2>&1 || true
{ dpkg --get-selections || true; } > "$SNAP/packages/dpkg_selections.txt" 2>&1 || true
{ snap list || true; } > "$SNAP/packages/snap_list.txt" 2>&1 || true
# APT 소스
mkdir -p "$SNAP/etc_apt"
cp -a /etc/apt/sources.list* "$SNAP/etc_apt/" 2>/dev/null || true

# 3) 계정별 크론/쉘 설정
mkdir -p "$SNAP/home_configs"
{ crontab -l || true; } > "$SNAP/home_configs/crontab_current_user.txt" 2>&1 || true
for f in ~/.bashrc ~/.zshrc ~/.profile ~/.bash_profile ~/.bash_aliases; do
  [ -f "$f" ] && cp -a "$f" "$SNAP/home_configs/"
done

# 4) 시스템 네트워크 상태(qdisc/iptables/sysctl)
mkdir -p "$SNAP/network"
{ ip addr; ip route; } > "$SNAP/network/ip_state.txt" 2>&1 || true
{ iptables-save || true; } > "$SNAP/network/iptables-save.txt" 2>&1 || true
{ nft list ruleset || true; } > "$SNAP/network/nft_ruleset.txt" 2>&1 || true
{
  echo "==== qdisc all interfaces ===="
  for dev in $(ls /sys/class/net); do
    echo "---- $dev ----"
    tc -s qdisc show dev "$dev" || true
  done
} > "$SNAP/network/qdisc_all.txt" 2>&1 || true
{ sysctl -a || true; } > "$SNAP/network/sysctl-all.txt" 2>&1 || true

# 5) Nginx/웹루트/서비스
mkdir -p "$SNAP/nginx" "$SNAP/systemd"
cp -a /etc/nginx "$SNAP/nginx/" 2>/dev/null || true
# /usr/local/nginx 경로 사용하는 경우
cp -a /usr/local/nginx/conf "$SNAP/nginx/usr_local_conf" 2>/dev/null || true
cp -a /usr/local/nginx/html "$SNAP/nginx/usr_local_html" 2>/dev/null || true

# 등록된 서비스들(사용자 정의 서비스 포함)
systemctl list-unit-files --type=service > "$SNAP/systemd/unit_files.txt" 2>&1 || true
# 로컬 서비스 파일 백업
cp -a /etc/systemd/system "$SNAP/systemd/etc_systemd_system" 2>/dev/null || true

# 6) 실행 중 Docker가 있다면 메타 저장
mkdir -p "$SNAP/docker"
{ docker ps -a || true; } > "$SNAP/docker/ps-a.txt" 2>&1 || true
{ docker image ls || true; } > "$SNAP/docker/images.txt" 2>&1 || true
# 용량 커질 수 있어 이미지 실제 저장은 스킵(필요 시 docker save)

# 7) 현재 작업 디렉토리 코드 스냅샷(깃 무시파일 제외하고 통째로)
# 원하면 특정 프로젝트 루트에서 실행해도 좋음
CODE_OUT="$SNAP/code_snapshot"
mkdir -p "$CODE_OUT"
# 대용량 파일은 제외하고 싶으면 --exclude 추가해서 조절
rsync -a --delete --exclude ".git" --exclude "__pycache__" ./ "$CODE_OUT/"

# 8) 유용한 실행 스크립트/환경 파일 자동 추출(있으면)
# 예: .env, *.service, *.sh, package.json, requirements.txt 등
find "$CODE_OUT" -maxdepth 3 -type f \( -name ".env" -o -name "package.json" -o -name "package-lock.json" -o -name "requirements*.txt" -o -name "*.service" -o -name "*.sh" \) \
  -printf "%P\n" > "$SNAP/code_snapshot/interesting_files.txt" 2>/dev/null || true

# 9) 복구 가이드 초안
cat > "$SNAP/RESTORE_NOTES.txt" <<'EOF'
[빠른 복구 순서]
1) OS가 깨끗한 Ubuntu라면:
   - APT 소스 점검: etc_apt/
   - 수동 설치 패키지: sudo xargs -a packages/apt_manual.txt apt-get install -y
   - (또는) dpkg --set-selections < packages/dpkg_selections.txt && sudo apt-get dselect-upgrade
   - snap 패키지: packages/snap_list.txt 참고 재설치
2) 드라이버/툴체인:
   - versions/ 아래의 nvidia_smi.txt, cuda.txt, ffmpeg.txt 참고해 동일/호환 버전 설치
3) 시스템 설정 복구:
   - /etc/nginx, /usr/local/nginx/* → nginx/ 백업본과 비교/반영 후 nginx -t && systemctl reload nginx
   - systemd 서비스는 systemd/etc_systemd_system/*.service 복사 → systemctl daemon-reload → enable/start
   - 네트워크 qdisc/iptables는 network/qdisc_all.txt, iptables-save.txt 참고해 스크립트화
   - sysctl-all.txt에서 커스텀 값만 골라 /etc/sysctl.d/10-custom.conf 등에 반영 후 sysctl --system
4) 코드/의존성:
   - code_snapshot/ 로 이동 → Python: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
   - Node: npm ci (package-lock.json가 있을 경우), 없으면 npm install
5) 서비스 실행:
   - 문서화된 방식(uvicorn/pm2/systemd/docker-compose 등)으로 실행

Tip) 장기적으론 Dockerfile/Compose로 런타임 고정 추천.
EOF

# 10) 압축
cd "$(dirname "$SNAP")"
tar -czf "$(basename "$SNAP").tar.gz" "$(basename "$SNAP")"
echo "==> Done. Archive: $SNAP.tar.gz"

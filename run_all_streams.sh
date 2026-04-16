declare -a NUMBERS=("11" "8" "9" "19")
CAM="Cam1"  # 대문자 주의

for num in "${NUMBERS[@]}"
do
  echo "▶ Starting player $num from $CAM"
  python3 stream_player.py "$num" "$CAM" &
done

wait

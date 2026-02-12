from decimal import Decimal, ROUND_HALF_UP

def nws_round(f_temp):
    return int(Decimal(str(f_temp)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

test_cases = [34.5, 35.5, 36.5, 34.4, 34.6]

print(f"{'F_Temp':<10} | {'Python round()':<15} | {'NWS Strategy (Correct)':<25} | {'Difference?'}")
print("-" * 75)

for t in test_cases:
    py_r = round(t)
    nws_r = nws_round(t)
    diff = "❌ DIFFERENT" if py_r != nws_r else "✅ SAME"
    print(f"{t:<10} | {py_r:<15} | {nws_r:<25} | {diff}")

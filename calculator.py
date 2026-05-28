import math

def get_attendance_pct(present: int, absent: int) -> float:
    """Return attendance percentage (ignores cancelled classes)."""
    total = present + absent
    if total == 0:
        return 0.0
    return (present / total) * 100

def classes_can_bunk(present: int, absent: int, target: float) -> int:
    """
    How many MORE classes can be bunked while staying >= target%?
    """
    total = present + absent
    current_pct = get_attendance_pct(present, absent)
    if current_pct < target:
        return 0   # Already below target — can't bunk anything
    max_bunk = (present * 100 - target * total) / target
    return max(0, math.floor(max_bunk))

def classes_must_attend(present: int, absent: int, target: float) -> int:
    """
    How many consecutive classes must be attended to reach target%?
    """
    total = present + absent
    current_pct = get_attendance_pct(present, absent)
    if current_pct >= target:
        return 0   # Already at or above target
    if total == 0:
        return 1   # Must attend at least 1 class to get any attendance record
    if target >= 100 and absent > 0:
        return float("inf")   # Mathematically impossible to reach 100% if we missed a class
    
    numerator = target * total - 100 * present
    denominator = 100 - target
    
    if denominator <= 0:
        return float("inf")
        
    return math.ceil(numerator / denominator)

def status_emoji(pct: float, target: float) -> str:
    """Return a visual indicator based on attendance %."""
    if pct >= target + 10:
        return "🟢"   # Plenty of buffer
    elif pct >= target:
        return "🟡"   # At target, be careful
    elif pct >= target - 10:
        return "🔴"   # Just below — danger zone
    else:
        return "💀"   # Critical

def projected_bunks(present: int, absent: int, target: float, remaining_classes: int) -> dict:
    """
    Calculates the true semester projection based on remaining scheduled classes.
    """
    total_semester_classes = present + absent + remaining_classes
    if total_semester_classes == 0:
        return {"status": "possible", "can_bunk": 0}
        
    needed_present = math.ceil(total_semester_classes * target / 100.0)
    
    if present + remaining_classes < needed_present:
        return {"status": "impossible", "shortfall": needed_present - (present + remaining_classes)}
        
    can_bunk = total_semester_classes - needed_present - absent
    return {"status": "possible", "can_bunk": max(0, can_bunk)}

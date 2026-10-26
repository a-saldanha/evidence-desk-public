"""Test fixture: a transcription of the itinerary screenshot (1791585897571_image.png) rendered as the kind of
markdown table LlamaParse returns for a spreadsheet image. Used to test the grid parser without an API key;
in a real run the markdown comes from LlamaParse."""
SPANS = {  # date -> [(start, end, label)]
    "11/09/26": [("18:00", "19:30", "Bombay to Doha"), ("19:30", "21:30", "TIME GAIN"), ("21:30", "23:00", "Bombay to Doha"), ("23:00", "24:00", "LAYOVER")],
    "12/09/26": [("0:00", "1:30", "LAYOVER"), ("1:30", "8:30", "Doha to Helsinki"), ("8:30", "9:00", "COLLECT LUGGAGE"),
                 ("9:00", "12:00", "STAY IN AIRPORT?"), ("12:00", "12:30", "EXIT AIRPORT"), ("12:30", "15:00", "BUS TO TAMPERE"),
                 ("15:00", "15:30", "CHECKIN AIRBNB")],
    "13/09/26": [("10:30", "11:00", "COFFEE BREAK"), ("12:30", "14:30", "LUNCH"), ("14:30", "16:00", "Quantum Computing:"),
                 ("16:00", "16:30", "COFFEE BREAK"), ("18:30", "20:30", "WELCOME RECEPTION")],
    "14/09/26": [("9:30", "10:30", "Plenary 1: Ulugbek Kamilov"), ("10:30", "11:00", "COFFEE BREAK"), ("11:30", "13:30", "Student Job Fair"),
                 ("13:30", "14:00", "LUNCH"), ("14:00", "16:00", "Reshu Ma'am Presentation"), ("16:00", "16:30", "COFFEE BREAK"),
                 ("18:00", "20:00", "Professionals Networking")],
    "15/09/26": [("10:30", "11:00", "COFFEE BREAK"), ("13:00", "14:00", "LUNCH"), ("16:00", "16:30", "COFFEE BREAK"),
                 ("16:30", "18:30", "ESCAN Presentation"), ("18:30", "24:00", "Conference Gala")],
    "16/09/26": [("10:30", "11:00", "COFFEE BREAK"), ("13:00", "14:00", "LUNCH"), ("14:00", "16:00", "Dhanunjay Sir Presentation"),
                 ("16:00", "16:30", "COFFEE BREAK")],
    "17/09/26": [("10:00", "10:30", "COFFEE BREAK"), ("11:30", "12:00", "CHECKOUT AIRBNB"), ("12:00", "13:30", "LUNCH"), ("15:00", "15:30", "COFFEE BREAK")],
    "18/09/26": [],
    "19/09/26": [("6:00", "17:00", "Helsinki to Doha"), ("17:00", "20:30", "LAYOVER"), ("20:30", "22:00", "Doha to Bombay"), ("22:00", "24:00", "TIME LOST")],
    "20/09/26": [("0:00", "1:30", "TIME LOST"), ("1:30", "3:00", "Doha to Bombay")],
}
DAYS = {"11/09/26": "Friday", "12/09/26": "Saturday", "13/09/26": "Sunday", "14/09/26": "Monday", "15/09/26": "Tuesday",
        "16/09/26": "Wednesday", "17/09/26": "Thursday", "18/09/26": "Friday", "19/09/26": "Saturday", "20/09/26": "Sunday"}


def _m(t):
    h, m = map(int, t.split(":")); return h * 60 + m


def markdown() -> str:
    cols = list(SPANS)
    lines = ["| date | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1),
             "| day | " + " | ".join(DAYS[c] for c in cols) + " |"]
    for k in range(48):
        a, b = k * 30, k * 30 + 30
        slot = f"{a // 60}:{a % 60:02d} - {(b // 60) % 24}:{b % 60:02d}"
        row = []
        for c in cols:
            lab = next((l for s, e, l in SPANS[c] if _m(s) <= a < _m(e)), "")
            row.append(lab)
        lines.append(f"| {slot} | " + " | ".join(row) + " |")
    return "\n".join(lines)

import sqlite3

conn = sqlite3.connect("/tmp/tickets.db")
cursor = conn.cursor()

cursor.execute("SELECT * FROM tickets")
rows = cursor.fetchall()

print("=" * 50)
print("Tickets en la base de datos:")
print("=" * 50)
for row in rows:
    print(f"ID: {row[0]}")
    print(f"Descripción: {row[1]}")
    print(f"Estado: {row[2]}")
    print("-" * 30)

if not rows:
    print("No hay tickets en la base de datos.")

conn.close()

import sqlite3

for row in sqlite3.connect("scrape.db").execute("SELECT url, title, length(text) FROM pages"):   
    print(row)
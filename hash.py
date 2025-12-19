import hashlib

pw = "John@12345"

# SQL NVARCHAR uses UTF-16LE encoding internally
hash_sql_compatible = hashlib.sha256(pw.encode("utf-16le")).hexdigest().upper()

print("Python UTF-8   :", hashlib.sha256(pw.encode("utf-8")).hexdigest().upper())
print("Python UTF-16LE:", hash_sql_compatible)

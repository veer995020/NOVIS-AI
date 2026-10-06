Get-ChildItem -Path 'C:\Users\Renewfy\Links\NYRO' -Filter *.html -Recurse | ForEach-Object { (Get-Content $_.FullName) -replace 'chatify', 'Nyro' | Set-Content $_.FullName }

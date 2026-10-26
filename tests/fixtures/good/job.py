import schedule
schedule.every().day.do(job)
while True:
    schedule.run_pending()

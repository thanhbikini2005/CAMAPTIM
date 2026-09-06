from bot import classify_shark, calculate_poc, chunks

def test_colors():
    base={"A1":True,"A2":True,"blocked":False}
    assert classify_shark({**base,"condCount":7})=="PURPLE"
    assert classify_shark({**base,"condCount":6})=="YELLOW"
    assert classify_shark({**base,"condCount":4})=="GREEN"
    assert classify_shark({**base,"condCount":8,"blocked":True}) is None

def test_poc():
    bars=[{"low":10,"high":20,"volume":100},{"low":15,"high":25,"volume":200}]
    p=calculate_poc(bars)
    assert p is not None and 10 <= p <= 25

def test_chunks():
    assert all(len(x)<=3900 for x in chunks("a\n"*5000))

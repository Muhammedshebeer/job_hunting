import re
SKILLS = {'python':4,'django':5,'drf':3,'fastapi':4,'postgresql':4,'rest api':3,'react':2,'javascript':2,'ai':2,'llm':2,'stripe':1,'docker':1}
ALIASES = {'drf':['django rest framework','drf'],'rest api':['rest api','restful api','rest apis'],'ai':['artificial intelligence','ai','machine learning'],'llm':['llm','large language model']}
def contains(text, term):
    return any(re.search(r'(?<!\w)'+re.escape(s)+r'(?!\w)',text,re.I) for s in ALIASES.get(term,[term]))
def score_job(description, cv_text):
    # Explainable weighted skill overlap, not a calibrated probability.
    required=[(skill,weight) for skill,weight in SKILLS.items() if contains(description,skill)]
    if not required: return 0, {'reason':'No recognized technical requirements'}
    total=sum(weight for _,weight in required)
    found=[skill for skill,_ in required if contains(cv_text,skill)]
    points=sum(weight for skill,weight in required if skill in found)
    return round(100*points/total), {'required':[s for s,_ in required], 'matched':found}

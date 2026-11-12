"""Worldwide eligibility and UAE-first presentation use separate rules."""
import re


UAE = re.compile(
    r'\b(?:uae|u\.a\.e\.?|united arab emirates|dubai|abu dhabi|sharjah|'
    r'ajman|fujairah|ras al khaimah|umm al quwain)\b', re.I)


def known_location(location):
    return isinstance(location, str) and bool(location.strip())


def location_group(location):
    if not known_location(location):
        return 'unknown'
    return 'uae' if UAE.search(location) else 'other'


LOCATION_GROUPS = (('uae', 'UAE jobs — first priority'),
                   ('other', 'Other locations'), ('unknown', 'Location unverified'))


def location_priority(location):
    return {'uae': 0, 'other': 1, 'unknown': 2}[location_group(location)]


def prioritize_jobs(jobs):
    # Stable sort preserves the caller's ordering within each location group.
    return sorted(jobs, key=lambda job: location_priority(job.location))

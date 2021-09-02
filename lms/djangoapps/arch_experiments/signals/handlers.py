from common.djangoapps.student.signals.signals import (
    UNENROLL_DONE
)
from django.dispatch import receiver
from pprint import pprint

@receiver(UNENROLL_DONE)
def transmit_course_grade_change_to_event_bus(**kwargs):
    print("="*80)
    print("="*80)
    pprint(kwargs)
    print("="*80)
    print("="*80)

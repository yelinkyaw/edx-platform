from common.djangoapps.student.signals.signals import (
    UNENROLL_DONE
)
from django.dispatch import receiver
from pprint import pprint
from django.conf import settings
import json

ARCH_EXPERIMENT_TOPIC="test_topic"

@receiver(UNENROLL_DONE)
def transmit_unenrollment_to_event_bus(**kwargs):
    print("="*80)
    print("="*80)
    pprint(kwargs)
    print("="*80)
    print("="*80)
    enrollment = kwargs['course_enrollment']
    enrollment_data = {
        "user_id": enrollment.user.id,
        "course": str(enrollment.course.id),
        "mode": enrollment.mode,
    }
    settings.KAFKA_PRODUCER.produce(ARCH_EXPERIMENT_TOPIC,
            key=str(kwargs['course_enrollment'].id),
            value=json.dumps(enrollment_data)
        )

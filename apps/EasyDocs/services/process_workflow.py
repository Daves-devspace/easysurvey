# apps/EasyDocs/services/process_workflow.py

from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import ClientServiceProcess, ServiceCategory

class ProcessWorkflowService:
    """Orchestrates completing one step and advancing the workflow."""

    def __init__(self, client_service):
        self.cs = client_service
        self.steps = list(self.cs.service_processes.order_by('process__step_order'))

    def complete_step(self, step: ClientServiceProcess):
        """
        Mark `step` completed, advance next steps, update CS status,
        and send any SMS notifications based on workflow rules.
        """
        with transaction.atomic():
            # 1️⃣ Complete the current step
            if step.status != 'completed':
                step.status = 'completed'
                step.completed_at = timezone.now()
                step.save(update_fields=['status', 'completed_at'])

            # 2️⃣ Ensure all previous steps are completed
            for s in self.steps:
                if s.process.step_order < step.process.step_order and s.status != 'completed':
                    raise ValueError("Previous steps must be completed first")

            # 3️⃣ Advance the next step
            last_completed_order = step.process.step_order
            next_steps = [s for s in self.steps if s.process.step_order == last_completed_order + 1]
            if next_steps:
                nxt = next_steps[0]
                if nxt.status == 'pending':
                    nxt.status = 'in_progress'
                    nxt.save(update_fields=['status'])

                    # ✅ Send SMS only if this is NOT the last step
                    if nxt != self.steps[-1]:
                        self._send_sms(
                            nxt,
                            reason=f"{self.cs.service.name} – process: {nxt.process.name}"
                        )

            # 4️⃣ Update the overall ClientService status
            all_done = all(s.status in ['completed', 'pending'] for s in self.steps)
            new_cs_status = 'completed' if all_done else 'active'
            if self.cs.status != new_cs_status:
                self.cs.status = new_cs_status
                self.cs.save(update_fields=['status'])

            # 5️⃣ Final SMS when last step is completed
            if step == self.steps[-1] and self.cs.status == 'completed':

                self._send_sms(
                    step,
                    message=step.process.message,
                    reason=f"{self.cs.service.name} – final process: {step.process.name}"
                )

    def _send_sms(self, step: ClientServiceProcess, message=None, reason=None):
        phone = self.cs.client.phone
        msg = message or step.process.message
        if phone and msg:
            log = send_and_log_sms(
                client_service=self.cs,
                client=self.cs.client,
                phone=phone,
                message=msg,
                reason=reason or f"{self.cs.service.name}"
            )
            return log
        return None

# apps/EasyDocs/services/process_workflow.py

from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import ClientServiceProcess, ServiceCategory

import logging
from django.db import transaction
from django.utils import timezone

import logging
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

class ProcessWorkflowService:
    """Orchestrates completing one step and advancing the workflow."""
    def __init__(self, client_service):
        self.cs = client_service
        # Load steps in order
        self.steps = list(self.cs.service_processes.order_by('process__step_order'))

    def complete_step(self, step: ClientServiceProcess):
        """
        Mark `step` completed, advance next steps, update CS status,
        and send any SMS notifications based on workflow rules.
        Returns the MessageLog for any SMS sent, else None.
        """
        sms_log = None
        logger.info("=== complete_step start for CS #%s, step '%s' ===",
                    self.cs.id, step.process.name)

        with transaction.atomic():
            # 1️⃣ Complete the current step
            if step.status != 'completed':
                step.status = 'completed'
                step.completed_at = timezone.now()
                step.save(update_fields=['status', 'completed_at'])
                logger.info("Marked '%s' completed at %s",
                            step.process.name, step.completed_at)

                # --- Sync in-memory list so statuses reflect reality ---
                for s in self.steps:
                    if s.pk == step.pk:
                        s.status = 'completed'
                        s.completed_at = step.completed_at
                        logger.debug("Synced in-memory step '%s' to completed", s.process.name)
                        break

            # 2️⃣ Enforce sequential ordering
            for s in self.steps:
                if s.process.step_order < step.process.step_order and s.status != 'completed':
                    logger.warning("Cannot complete '%s' before '%s'",
                                   step.process.name, s.process.name)
                    raise ValueError("Previous steps must be completed first")

            # 3️⃣ Advance the next step into in_progress
            last_order = step.process.step_order
            next_steps = [s for s in self.steps if s.process.step_order == last_order + 1]
            if next_steps:
                nxt = next_steps[0]
                logger.info("Next step: '%s' (status=%s)", nxt.process.name, nxt.status)
                if nxt.status == 'pending':
                    nxt.status = 'in_progress'
                    nxt.save(update_fields=['status'])
                    logger.info("Advanced '%s' to in_progress", nxt.process.name)

                    # Only notify if this next step is NOT the last step
                    if nxt != self.steps[-1]:
                        logger.info("Sending SMS for '%s'", nxt.process.name)
                        sms_log = self._send_sms(
                            nxt,
                            reason=f"{self.cs.service.name} – process: {nxt.process.name}"
                        )
                        logger.info("SMS log created: %s", sms_log)
                    else:
                        logger.info("Skipped SMS for last-step activation '%s'", nxt.process.name)
            else:
                logger.info("No next step to advance")

            # --- DEBUG: dump all step statuses to confirm sync ---
            statuses = [(s.process.name, s.status) for s in self.steps]
            logger.info("All step statuses after advance: %s", statuses)

            # 4️⃣ Update overall ClientService status
            all_done = all(s.status == 'completed' for s in self.steps)
            logger.info("All steps completed? %s", all_done)
            new_status = 'completed' if all_done else 'active'
            if self.cs.status != new_status:
                self.cs.status = new_status
                self.cs.save(update_fields=['status'])
                logger.info("ClientService status set to '%s'", new_status)

            # 5️⃣ Send final SMS when last step is completed
            is_last = (step == self.steps[-1])
            logger.info("Is this the last step? %s; CS.status='%s'", is_last, self.cs.status)
            if is_last and self.cs.status == 'completed':
                logger.info("Sending final SMS for '%s'", step.process.name)
                sms_log = self._send_sms(
                    step,
                    message=step.process.message,
                    reason=f"{self.cs.service.name} – final process: {step.process.name}"
                )
                logger.info("Final SMS log returned: %s", sms_log)
            else:
                logger.info("No final SMS sent for '%s'", step.process.name)

        logger.info("=== complete_step end, returning sms_log: %s ===", sms_log)
        return sms_log

    def _send_sms(self, step, message=None, reason=None):
        """
        Wrapper around your SMS API that returns the MessageLog.
        """
        phone = self.cs.client.phone
        msg = message or step.process.message
        logger.debug("Attempting SMS: phone=%s, msg=%r, reason=%r", phone, msg, reason)
        if phone and msg:
            log = send_and_log_sms(
                client_service=self.cs,
                client=self.cs.client,
                phone=phone,
                message=msg,
                reason=reason or f"{self.cs.service.name}"
            )
            logger.debug("send_and_log_sms returned: %s", log)
            return log
        logger.warning(
            "SMS not sent for '%s': missing phone or message (phone=%s, msg_empty=%s)",
            step.process.name, phone, not bool(msg)
        )
        return None



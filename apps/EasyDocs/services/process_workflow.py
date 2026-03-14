# apps/EasyDocs/services/process_workflow.py

import logging

from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import (
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    ServiceCategory,  # noqa: F401 – kept for import compat
)
from apps.notifications.models import Notification
from apps.notifications.utils import send_push_to_user

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

        PR-06: Consensus check – if active process-assignments exist for this
        step, all accepted assignees must have completion_status='completed'
        before the step advances.  Legacy flows (no assignment rows) proceed
        unconditionally.
        """
        sms_log = None
        logger.info("=== complete_step start for CS #%s, step '%s' ===",
                    self.cs.id, step.process.name)

        with transaction.atomic():
            # 1-Consensus check
            if not self._consensus_reached(step):
                logger.info(
                    "complete_step: consensus not yet reached for step '%s' -- "
                    "waiting for remaining assignees.",
                    step.process.name,
                )
                return None

            # 2-Complete the current step
            if step.status != 'completed':
                step.status = 'completed'
                step.completed_at = timezone.now()
                step.save(update_fields=['status', 'completed_at'])
                logger.info("Marked '%s' completed at %s",
                            step.process.name, step.completed_at)

                # Sync in-memory list so subsequent checks reflect DB state
                for s in self.steps:
                    if s.pk == step.pk:
                        s.status = 'completed'
                        s.completed_at = step.completed_at
                        logger.debug("Synced in-memory step '%s' to completed",
                                     s.process.name)
                        break

            # 3-Enforce sequential ordering
            for s in self.steps:
                if (s.process.step_order < step.process.step_order
                        and s.status != 'completed'):
                    logger.warning("Cannot complete '%s' before '%s'",
                                   step.process.name, s.process.name)
                    raise ValueError("Previous steps must be completed first")

            # 4-Advance the next actionable step into in_progress
            last_order = step.process.step_order
            next_actionable = next(
                (s for s in self.steps
                 if s.process.step_order > last_order
                 and s.status not in ('completed', 'collected')),
                None,
            )
            if next_actionable:
                logger.info("Next actionable step: '%s' (status=%s)",
                            next_actionable.process.name, next_actionable.status)
                if next_actionable.status == 'pending':
                    next_actionable.status = 'in_progress'
                    next_actionable.save(update_fields=['status'])
                    logger.info("Advanced '%s' to in_progress",
                                next_actionable.process.name)

                    notified_count = self._notify_next_step_assignees(next_actionable)
                    logger.info(
                        "Notified %s assignee(s) for step '%s' activation",
                        notified_count,
                        next_actionable.process.name,
                    )

                    if next_actionable != self.steps[-1]:
                        logger.info("Sending SMS for '%s'",
                                    next_actionable.process.name)
                        sms_log = self._send_sms(
                            next_actionable,
                            reason=(f"{self.cs.service.name} - process: "
                                    f"{next_actionable.process.name}"),
                        )
                        logger.info("SMS log created: %s", sms_log)
                    else:
                        logger.info("Skipped SMS for last-step activation '%s'",
                                    next_actionable.process.name)
            else:
                logger.info("No next actionable step to advance")

            # Debug: dump all step statuses
            statuses = [(s.process.name, s.status) for s in self.steps]
            logger.info("All step statuses after advance: %s", statuses)

            # 5-Update overall ClientService status
            all_done = all(s.status == 'completed' for s in self.steps)
            logger.info("All steps completed? %s", all_done)
            new_status = 'completed' if all_done else 'active'
            if self.cs.status != new_status:
                self.cs.status = new_status
                self.cs.save(update_fields=['status'])
                logger.info("ClientService status set to '%s'", new_status)

            # 6-Send final SMS when last step is completed
            is_last = (step == self.steps[-1])
            logger.info("Is this the last step? %s; CS.status='%s'",
                        is_last, self.cs.status)
            if is_last and self.cs.status == 'completed':
                logger.info("Sending final SMS for '%s'", step.process.name)
                sms_log = self._send_sms(
                    step,
                    message=step.process.message,
                    reason=(f"{self.cs.service.name} - final process: "
                            f"{step.process.name}"),
                )
                logger.info("Final SMS log returned: %s", sms_log)
            else:
                logger.info("No final SMS sent for '%s'", step.process.name)

        logger.info("=== complete_step end, returning sms_log: %s ===", sms_log)
        return sms_log

    # ------------------------------------------------------------------
    # PR-06: Consensus helper
    # ------------------------------------------------------------------

    @staticmethod
    def _consensus_reached(step: ClientServiceProcess) -> bool:
        """
        Return True when it is safe to advance this step.

        Rules:
          - If no active process-assignments exist for this step, return True
            (legacy / single-assignee flows have nothing to block on).
          - Otherwise: accepted_count > 0 AND every accepted assignee has
            completion_status == 'completed'.
        """
        active_qs = ClientServiceProcessAssignment.objects.filter(
            client_service_process=step,
            is_active=True,
        )
        if not active_qs.exists():
            return True  # no assignment rows -- legacy path, always proceed

        accepted_qs = active_qs.filter(acceptance_status="accepted")
        accepted_count = accepted_qs.count()
        if accepted_count == 0:
            return False  # assignments exist but none accepted yet

        completed_count = accepted_qs.filter(completion_status="completed").count()
        return completed_count == accepted_count

    # ------------------------------------------------------------------

    def _notify_next_step_assignees(self, step: ClientServiceProcess) -> int:
        """
        Notify active assignees when a process step becomes in_progress.

        Returns number of assignees successfully queued for notification.
        """
        assignments = (
            ClientServiceProcessAssignment.objects
            .filter(client_service_process=step, is_active=True)
            .select_related("assignee")
        )

        assignees = []
        seen_user_ids = set()
        for assignment in assignments:
            assignee = assignment.assignee
            if not assignee or assignee.id in seen_user_ids:
                continue
            seen_user_ids.add(assignee.id)
            assignees.append(assignee)

        if not assignees:
            return 0

        client_name = f"{self.cs.client.first_name} {self.cs.client.last_name}".strip()
        notify_title = "Process In Progress"
        notify_body = (
            f"{self.cs.service.name} for {client_name}: "
            f"{step.process.name} is now in progress."
        )

        notified = 0
        for assignee in assignees:
            try:
                Notification.objects.create(
                    user=assignee,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(assignee, notify_title, notify_body)
                notified += 1
            except Exception:
                logger.exception(
                    "Failed to notify assignee %s for process step %s",
                    assignee.id,
                    step.id,
                )

        return notified

    # ------------------------------------------------------------------

    def _send_sms(self, step, message=None, reason=None):
        """Wrapper around send_and_log_sms; returns MessageLog or None."""
        if not step.process.notification_enabled:
            logger.info("Skipping SMS for '%s': notifications disabled on process",
                        step.process.name)
            return None

        if step.completed_at_onboarding:
            logger.info("Skipping SMS for '%s': process completed at onboarding",
                        step.process.name)
            return None

        phone = self.cs.client.phone
        msg = message or step.process.message
        logger.debug("Attempting SMS: phone=%s, msg=%r, reason=%r", phone, msg, reason)
        if phone and msg:
            log = send_and_log_sms(
                client_service=self.cs,
                client=self.cs.client,
                phone=phone,
                message=msg,
                reason=reason or f"{self.cs.service.name}",
            )
            logger.debug("send_and_log_sms returned: %s", log)
            return log
        logger.warning(
            "SMS not sent for '%s': missing phone or message (phone=%s, msg_empty=%s)",
            step.process.name, phone, not bool(msg),
        )
        return None

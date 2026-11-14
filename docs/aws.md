# AWS sandbox notes (Person C)

Fill this in when we get into the Workshop Studio sandbox. It decides whether Milestone 3 (Rekognition Face Liveness as the additional verification step) is possible.

## What we know before checking

- Face Liveness needs the Amplify `FaceLivenessDetector` component in the browser. There is no way to use it without the Amplify SDK.
- AWS lists Face Liveness in five regions: US East (N. Virginia) `us-east-1`, US West (Oregon) `us-west-2`, Europe (Ireland) `eu-west-1`, Asia Pacific (Tokyo) `ap-northeast-1` and Asia Pacific (Mumbai) `ap-south-1`. Mumbai is closest to us, so try it first if the sandbox allows it.
- The `FaceMovementChallenge` setting (no coloured light) is set in `CreateFaceLivenessSession`. We use it so our own screen-flash check stays separate.
- If audit and reference images go to S3, the bucket must be in the same region as Face Liveness. Otherwise receive them as bytes.
- Face Liveness is charged per check. Confirm the sandbox covers it.

## Checklist

| Item | How to check | Result |
| --- | --- | --- |
| Account ID and sandbox expiry | Workshop Studio event page | |
| Allowed regions | Try opening Rekognition in `ap-south-1`, then `us-east-1` | |
| `rekognition:CreateFaceLivenessSession` | `aws rekognition create-face-liveness-session --region <r> --settings '{"ChallengePreferences":[{"Type":"FaceMovementChallenge"}]}'` | |
| `rekognition:GetFaceLivenessSessionResults` | Call with the session ID from the step above (expect a CREATED or EXPIRED status, not AccessDenied) | |
| `rekognition:StartFaceLivenessSession` for the browser | Needs a Cognito identity pool, or temporary credentials the backend hands to the kiosk | |
| `rekognition:CompareFaces` | For comparing the liveness reference image with the enrolled identity, if we do this on AWS | |
| Cognito identity pools allowed | Console → Cognito → Create identity pool | |
| S3 bucket creation in the same region | Only needed if we want audit images in S3 | |
| IAM role creation allowed | Console → IAM → Roles | |
| Service quotas / throttling | Note any errors during rehearsal | |
| Latency from the venue Wi-Fi | Time from opening the component to the result, 5 tries | median ___ ms, max ___ ms |

## Decisions

- Region used: ____
- How the browser gets credentials (Cognito identity pool or backend-issued): ____
- Data handling: delete liveness sessions' audit images after the event. Note whether the account opted out of AWS using inputs for service improvement.

## Timing

Report the initial outcome (our local pipeline, two-second target) separately from the total additional-verification time (Face Liveness). Confirm with the organizers whether the two-second target applies only to the initial outcome: ____.

#!/bin/bash -x

# Assume the role
STS_OUTPUT=$(aws sts assume-role --role-arn ${EXECUTION_ROLE_ARN} --role-session-name Maskopy --output text | sed -n '2 p')
export AWS_ACCESS_KEY_ID=$(echo "${STS_OUTPUT}" | cut -f2)
export AWS_SECRET_ACCESS_KEY=$(echo "${STS_OUTPUT}" | cut -f4)
export AWS_SESSION_TOKEN=$(echo "${STS_OUTPUT}" | cut -f5)
export AWS_SECURITY_TOKEN=$(echo "${STS_OUTPUT}" | cut -f5)

#Check inputs
echo "Validating mandatory inputs"

if [[ -z ${APPLICATION_NAME} || -z ${DESTINATION_ENV} || -z ${RDS_SNAPSHOT_IDENTIFIER} ]]; then
    echo "!! ERROR !! One of the required inputs is missing. Please check."
    exit 1
else
  echo "APPLICATION_NAME, DESTINATION_ENV, RDS_SNAPSHOT_IDENTIFIER inputs available, proceeding "

fi

BUILD_TIMESTAMP=$(date +%Y%m%d%H%M%S)
EXEC_NAME=${APPLICATION_NAME}-${BUILD_TIMESTAMP}
URL=$(python3 -c "import boto3; print(boto3.client('sts').generate_presigned_url('get_caller_identity'))")

if [[ -z ${URL} ]]; then
    echo "URL generation failed, stopping!!"
    exit 1
fi

echo "Executing stateMachine: ${STEP_FN_ARN}"
# executionArn=$(aws stepfunctions start-execution --query 'executionArn' --cli-input-json ' { "stateMachineArn": "'${STEP_FN_ARN}'", "name": "'${EXEC_NAME}'", "input": "{\"ApplicationName\": \"'${APPLICATION_NAME}'\",\"CostCenter\": \"'${COST_CENTER}'\",\"DestinationEnv\": \"'${DESTINATION_ENV}'\",\"RdsSnapshotIdentifier\":\"'${RDS_SNAPSHOT_IDENTIFIER}'\",\"RdsOptionGroup\": \"'${RDS_OPTION_GROUP}'\",\"RdsParameterGroup\":\"'${RDS_PARAMETER_GROUP}'\",\"ObfuscationScriptPath\":\"'${OBFUSCATION_SCRIPT_PATH}'\",\"PresignedUrl\":\"'${URL}'\" }" } ' | tr -d '"')
# if [ "$?" -ne 0 ]
# then
#     echo "Deployment failed!"
#     exit 1
# fi

export MASKOPY_SEQUENTIAL=1

(
cat <<jsonheredoc
{ 
    "sequential": ${MASKOPY_SEQUENTIAL}, 
    "MASKOPY00AuthorizeUserURI": "${MASKOPY00AuthorizeUserURI}",
    "MASKOPY01UseExistingSnapshotURI": "${MASKOPY01UseExistingSnapshot}",
    "MASKOPY02CheckForSnapshotCompletionURI": "${MASKOPY02CheckForSnapshotCompletion}",
    "MASKOPY03ShareSnapshotsURI": "${MASKOPY03ShareSnapshots}",
    "MASKOPY04CopySharedDBSnapshotsURI": "${MASKOPY04CopySharedDBSnapshots}",
    "MASKOPY05CheckForDestinationSnapshotCompletionURI": "${MASKOPY05CheckForDestinationSnapshotCompletion}",
    "MASKOPY06RestoreDatabasesURI": "${MASKOPY06RestoreDatabases}",
    "MASKOPY07CheckForRestoreCompletionURI": "${MASKOPY07CheckForRestoreCompletion}",
    "MASKOPY08aCreateFargateURI": "${MASKOPY08aCreateFargate}",
    "MASKOPY08aRunFargateTaskURI": "${MASKOPY08aRunFargateTask}",
    "MASKOPY08bWaitForFargateTaskURI": "${MASKOPY08bWaitForFargateTask}",
    "MASKOPY09TakeSnapshotURI": "${MASKOPY09TakeSnapshot}",
    "MASKOPY10CheckFinalSnapshotAvailabilityURI": "${MASKOPY10CheckFinalSnapshotAvailability}",
    "MASKOPY11CleanupAndTaggingURI": "${MASKOPY11CleanupAndTagging}",
    "MASKOPYErrorHandlingAndCleanupURI": "${MASKOPYErrorHandlingAndCleanup}",
    "ApplicationName": "${APPLICATION_NAME}",
    "CostCenter": "${COST_CENTER}",
    "DestinationEnv": "${DESTINATION_ENV}",
    "RdsSnapshotIdentifier":"${RDS_SNAPSHOT_IDENTIFIER}",
    "RdsOptionGroup": "${RDS_OPTION_GROUP}",
    "RdsParameterGroup":"${RDS_PARAMETER_GROUP}",
    "ObfuscationScriptPath":"${OBFUSCATION_SCRIPT_PATH}",
    "PresignedUrl":"${URL}" 
}
jsonheredoc
) > maskopy-input.json

echo -e """
\n\n\n\n\n
------------------------------------------------------
READ ME!!!!!!!!IMPORTANT!!!!!!!!!
------------------------------------------------------
Link to stateMachine: https://console.aws.amazon.com/states/home?region=${AWS_DEFAULT_REGION}#/statemachines/view/${STEP_FN_ARN}
Link to execution: https://console.aws.amazon.com/states/home?region=${AWS_DEFAULT_REGION}#/executions/details/${executionArn}
\n\n\n\n\n
"""

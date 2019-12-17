"""
Copyright (c) 2019. Maskopy Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.


This lambda cleans up any resources generated by the step function in case of Error.
This lambda expects the following inputs:
- ApplicationName
- DestinationEnv
- RdsSnapshotIdentifier

Optional:
- AsgName
- CreatedDestinationSnapshots
- CreatedSnapshots
- DestinationRestoredDatabases
- ecs
- fargate
- InstanceId
- ObfuscateRunMode
- TaskDefinition
"""
import json
import os
import time
import boto3
from botocore.exceptions import ClientError

ASG_CLIENT = boto3.client('autoscaling')
ECS_CLIENT = boto3.client('ecs')
RDS_CLIENT = boto3.client('rds')
STS_CLIENT = boto3.client('sts')
ASSUME_ROLE_ARN = os.environ['assume_role_arn']

def lambda_handler(event, context):
    """Lambda handler for the eleventh lambda of the Maskopy process.
    Args:
        event (dict): AWS Lambda uses this parameter to pass in event data to the handler.
        context (Context): AWS Lambda provides runtime info and meta data.
    Returns:
        :obj:`list` of :obj`dict` of str:str:
            List of deleted resources and message to be sent to SQS.
    """
    deleted_resources = []

    # Create message to be sent to SQS
    json_msg = {
        "ApplicationName": event['ApplicationName'],
        "State": "CRITICAL",
        "SDLC": event['DestinationEnv'],
        "Service": "MasKopy",
        "msgDetail": (f"MasKopy process for ApplicationName: {event['ApplicationName']} "
                      f"for snapshotID: {event['RdsSnapshotIdentifier']}. "
                      f"The status is: CRITICAL.")
    }
    deleted_resources.append({'Message' : json.dumps(json_msg)})

    session = create_account_session(
        STS_CLIENT, ASSUME_ROLE_ARN, context.aws_request_id)
    rds_source_client = session.client('rds')
    for shared_snapshot in event.get('CreatedSnapshots', []):
        if isinstance(shared_snapshot, dict):
            snapshot_name = shared_snapshot.get('SnapshotName')
            print(f"Deleting snapshot in source account: {snapshot_name}")
            if delete_snapshot(rds_source_client, snapshot_name):
                deleted_resources.append({'SourceSnapshot' : snapshot_name})

    for destination_snapshot in event.get('CreatedDestinationSnapshots', []):
        if isinstance(destination_snapshot, dict):
            snapshot_name = destination_snapshot.get('SnapshotName')
            print(f"Deleting snapshots in destination account: {snapshot_name}")
            if delete_snapshot(RDS_CLIENT, snapshot_name):
                deleted_resources.append({'DestinationSnapshot': snapshot_name})

    for database in event.get('DestinationRestoredDatabases', []):
        if database.startswith('maskopy'):
            print(f"Deleting RDS instance in destination account: {database}")
            if delete_database(RDS_CLIENT, database):
                deleted_resources.append({"DestinationDatabase": database})

    if event.get('ObfuscateRunMode') == 'ecs':
        ecs = event.get('ecs')
        if ecs:
            if (ecs.get('InstanceId') and ecs.get('AsgName') and
                    delete_asg(ASG_CLIENT, ecs['AsgName'])):
                deleted_resources.append({"Instance": ecs['InstanceId']})
                deleted_resources.append({"ASG": ecs['AsgName']})
            if (ecs.get('TaskDefinition') and
                    deregister_task_definition(ECS_CLIENT, ecs['TaskDefinition'])):
                deleted_resources.append({"Task Definition": ecs['TaskDefinition']})
            if (ecs.get('ClusterName') and
                    delete_cluster(ECS_CLIENT, ecs.get('ClusterName'), ecs.get('InstanceId'))):
                deleted_resources.append({"ECS Cluster": ecs['ClusterName']})
    elif not event.get('ObfuscateRunMode') or event.get('ObfuscateRunMode') == 'fargate':
        fargate = event.get('fargate')
        if (fargate and fargate.get('TaskDefinition') and
                deregister_task_definition(ECS_CLIENT, fargate.get('TaskDefinition'))):
            deleted_resources.append({"Task Definition": fargate.get('TaskDefinition')})


    return deleted_resources

def delete_snapshot(rds_client, snapshot_identifier):
    """Function to delete snapshot.
    Args:
        rds_client (Client): AWS RDS Client object.
        snapshot_identifier (str): RDS snapshot identifer to delete
    Returns:
        bool: True if snapshot was deleted successfully or does not exist,
            False otherwise.
    Raises:
        MaskopyResourceException: Exception used when trying to access a resource
            that cannot be accessed.
        MaskopyThrottlingException: Exception used to catch throttling from AWS.
            Used to implement a back off strategy.
    """
    try:
        rds_client.delete_db_snapshot(
            DBSnapshotIdentifier=snapshot_identifier)
        return True
    except ClientError as err:
        # Check if error code is DBSnapshotNotFound. If so, ignore the error.
        if err.response['Error']['Code'] == 'DBSnapshotNotFound':
            print(f'Snapshot, {snapshot_identifier}, already deleted.')
            return True
        # Check if error code is due to SNAPSHOT not being in an available state.
        if err.response['Error']['Code'] == 'InvalidDBSnapshotState':
            print(f"{snapshot_identifier}: RDS snapshot is not in available state.")
            raise MaskopyResourceException(err)
        # Check if error code is due to throttling.
        if err.response['Error']['Code'] == 'Throttling':
            print(f"Throttling occurred when deleting snapshot: {snapshot_identifier}.")
            raise MaskopyThrottlingException(err)
        print(f"Error deleting snapshot, {snapshot_identifier}: {err.response['Error']['Code']}.")
        print(err)
        return False

def delete_database(rds_client, db_instance_identifier):
    """Function to delete RDS instance.
    Args:
        rds_client (Client): AWS RDS Client object.
        db_instance_identifier (str): RDS instance to delete
    Returns:
        bool: True if instance was deleted successfully or does not exist,
            False otherwise.
    Raises:
        MaskopyResourceException: Exception used when trying to access a resource
            that cannot be accessed.
        MaskopyThrottlingException: Exception used to catch throttling from AWS.
            Used to implement a back off strategy.
    """
    try:
        rds_client.delete_db_instance(
            DBInstanceIdentifier=db_instance_identifier,
            SkipFinalSnapshot=True)
        return True
    except ClientError as err:
        # Check if error code is DBInstanceNotFound. If so, ignore the error.
        if err.response['Error']['Code'] == 'DBInstanceNotFound':
            print(f'RDS instance, {db_instance_identifier}, already deleted.')
            return True
        # Check if error code is due to RDS not being in an available state.
        if err.response['Error']['Code'] == 'InvalidDBInstanceState':
            print(f"{db_instance_identifier}: RDS instance is not in available state.")
            raise MaskopyResourceException(err)
        # Check if error code is due to throttling.
        if err.response['Error']['Code'] == 'Throttling':
            print(f"Throttling occurred when deleting database: {db_instance_identifier}.")
            raise MaskopyThrottlingException(err)
        print(f"Error deleting database, {db_instance_identifier}: {err.response['Error']['Code']}")
        print(err)
        return False

def delete_asg(asg_client, asg_name):
    """Function to delete ASG.
    Args:
        asg_client (Client): AWS ASG Client object.
        asg_name (str): ASG and launch configuration name to delete
    Returns:
        bool: True if instance was deleted successfully or does not exist,
            False otherwise.
    Raises:
        MaskopyResourceException: Exception used when trying to access a resource
            that cannot be accessed.
        MaskopyThrottlingException: Exception used to catch throttling from AWS.
            Used to implement a back off strategy.
    """
    try:
        # Check if ASG exists and then delete it
        asg_response = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name])
        if asg_response['AutoScalingGroups']:
            print(f'Deleting ASG: {asg_name}')
            asg_client.delete_auto_scaling_group(
                AutoScalingGroupName=asg_name, ForceDelete=True)
            time.sleep(40)

        # Check if launch configuration exists and then delete it
        launch_configuration_response = asg_client.describe_launch_configurations(
            LaunchConfigurationNames=[asg_name])
        if launch_configuration_response['LaunchConfigurations']:
            print(f'Deleting launch configuration: {asg_name}.')
            asg_client.delete_launch_configuration(
                LaunchConfigurationName=asg_name)

        return True
    except ClientError as err:
        # Check if error code is ResourceContention.
        if err.response['Error']['Code'] == 'ResourceContention':
            print(f"ASG or launch configuration has a pending update already: {asg_name}.")
            raise MaskopyResourceException(err)
        # Check if error code is ResourceInUse.
        if err.response['Error']['Code'] == 'ResourceInUse':
            print(f"Launch configuration is still in use: {asg_name}.")
            raise MaskopyResourceException(err)
        # Check if error code is due to throttling.
        if err.response['Error']['Code'] == 'Throttling':
            print(f"Throttling occurred when deleting ASG: {asg_name}.")
            raise MaskopyThrottlingException(err)
        print(f"Error deleting ASG, {asg_name}: {err.response['Error']['Code']}")
        print(err)
        return False

def deregister_task_definition(ecs_client, task_definition):
    """Function to deregister task definition.
    Args:
        ecs_client (Client): AWS ECS Client object.
        task_definition (str): Task definition to delete
    Returns:
        bool: True if task definition was deregistered successfully or does not exist,
            False otherwise.
    Raises:
        MaskopyResourceException: Exception used when trying to access a resource
            that cannot be accessed.
        MaskopyThrottlingException: Exception used to catch throttling from AWS.
            Used to implement a back off strategy.
    """
    try:
        print(f'Deregistering task definition: {task_definition}')
        ecs_client.deregister_task_definition(
            taskDefinition=task_definition)
        return True
    except ClientError as err:
        # Check if error code is ClientException.
        if (err.response['Error']['Code'] == 'ClientException' and
                err.response['Error']['Message'] ==
                'The specified task definition does not exist.'):
            print(f'Task definition revision, {task_definition}, does not exist.')
            return True
        print(f"Error deregistering task definition, {task_definition}: "
              f"{err.response['Error']['Code']}")
        print(err)
        return False

def delete_cluster(ecs_client, cluster_name, instance_identifier=None):
    """Function to delete ECS or fargate cluster.
    Args:
        ecs_client (Client): AWS ECS Client object.
        cluster_name (str): Cluster to delete
        instance_identifier (str, optional): Instance identifier to deregister.
            Classical ECS clusters require EC2 instance to be registered.
            Forcing a deregister of the instance allows the ECS cluster to be
            deleted.
    Returns:
        bool: True if cluster was deleted successfully or does not exist,
            False otherwise.
    Raises:
        MaskopyResourceException: Exception used when trying to access a resource
            that cannot be accessed.
        MaskopyThrottlingException: Exception used to catch throttling from AWS.
            Used to implement a back off strategy.
    """
    try:
        cluster = ecs_client.describe_clusters(
            clusters=[cluster_name])
        if instance_identifier:
            ecs_client.deregister_container_instance(
                cluster=cluster_name,
                containerInstance=instance_identifier,
                force=True)

        print('Deleting ECS Cluster:' + cluster_name)
        ecs_client.delete_cluster(cluster=cluster_name)
        return True
    except ClientError as err:
        # Check if error code is ClusterNotFoundException.
        if err.response['Error']['Code'] == 'ClusterNotFoundException':
            print(f'ECS cluster, {cluster_name}, already deleted.')
            return True
        # Check if error code is ClusterContainsContainerInstancesException.
        if err.response['Error']['Code'] == 'ClusterContainsContainerInstancesException':
            print(f'ECS cluster, {cluster_name}, still contains instances.')
            raise MaskopyResourceException(err)
        # Check if error code is ClusterContainsTasksException.
        if err.response['Error']['Code'] == 'ClusterContainsTasksException':
            print(f'ECS cluster, {cluster_name}, still contains tasks.')
            raise MaskopyResourceException(err)
        # Check if error code is due to throttling.
        if err.response['Error']['Code'] == 'Throttling':
            print(f"Throttling occurred when deleting ECS cluster: {cluster}.")
            raise MaskopyThrottlingException(err)
        print(f"Error deleting ECS, {cluster_name}: {err.response['Error']['Code']}")
        print(err)
        return False

def create_account_session(sts_client, role_arn, request_id):
    """Function to create and assume account role.
    Args:
        sts_client (Client): AWS STS Client object.
        role_arn (str): The arn of the role to assume a session.
        request_id (str): UUID for session to uniquely identify session name.
    Returns:
        :obj:`boto3.session.Session`:
            A session of the role to be used.
    """
    sts_response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=request_id
    )

    return boto3.session.Session(
        aws_access_key_id=sts_response['Credentials']['AccessKeyId'],
        aws_secret_access_key=sts_response['Credentials']['SecretAccessKey'],
        aws_session_token=sts_response['Credentials']['SessionToken']
    )

class MaskopyResourceException(Exception):
    """Exception raised when IAM role or user is not able to access the
    resource.
    """

class MaskopyThrottlingException(Exception):
    """Exception raised when AWS request returns a Throttling exception.
    """
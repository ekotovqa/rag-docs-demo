// Guard rails around the public demo, deployed once into the resource group after main.bicep:
//   - two metric alerts on the app (a burst of requests; a replica that has not slept for hours);
//   - a budget on this resource group that e-mails at 80% and, at 100% of actual spend, STOPS the app
//     through a Logic App whose managed identity may do exactly that and nothing else.
// The app's own limits (questions per visitor, per minute, money per day and month) live in the app.
//
//   az deployment group create -g rg-rag-docs-demo -f infra/guard.bicep -p @guard.parameters.json
//
// A stopped app answers nothing until `az containerapp start -n ragdemo-app -g rg-rag-docs-demo`.
targetScope = 'resourceGroup'

@description('Prefix of the demo resources.')
param namePrefix string = 'ragdemo'

@description('Where alert and budget e-mails go.')
param alertEmails array

@description('Monthly budget of this resource group in the subscription currency; at 100% of actual spend the app is stopped.')
param monthlyBudget int = 5

@description('First day of the budget period: the first of a month, YYYY-MM-DD.')
param budgetStart string

@description('Requests per 5 minutes that count as a burst worth an e-mail.')
param burstRequestsPer5Min int = 600

@description('Region of the Logic App.')
param location string = resourceGroup().location

resource app 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: '${namePrefix}-app'
}

var emailReceivers = [for (e, i) in alertEmails: { name: 'mail-${i}', emailAddress: e, useCommonAlertSchema: true }]
var roleContainerAppsContributor = '358470bc-b998-42bd-ab17-a7e34c199c0f'

// --- Who gets told ---

resource alerts 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${namePrefix}-alerts'
  location: 'global'
  properties: {
    groupShortName: 'ragdemo'
    enabled: true
    emailReceivers: emailReceivers
  }
}

// --- The cutoff: stop the app ---

resource cutoff 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${namePrefix}-cutoff'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {}
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: { schema: {} }
        }
      }
      actions: {
        stop_the_app: {
          type: 'Http'
          runAfter: {}
          inputs: {
            method: 'POST'
            uri: '${environment().resourceManager}${substring(app.id, 1)}/stop?api-version=2025-01-01'
            authentication: { type: 'ManagedServiceIdentity', audience: environment().resourceManager }
          }
        }
      }
      outputs: {}
    }
  }
}

resource cutoffRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(app.id, cutoff.id, roleContainerAppsContributor)
  scope: app
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleContainerAppsContributor)
    principalId: cutoff.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cutoffGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${namePrefix}-cutoff-ag'
  location: 'global'
  properties: {
    groupShortName: 'ragcutoff'
    enabled: true
    emailReceivers: emailReceivers
    logicAppReceivers: [
      {
        name: 'stop-the-app'
        resourceId: cutoff.id
        callbackUrl: listCallbackUrl('${cutoff.id}/triggers/manual', '2019-05-01').value
        useCommonAlertSchema: true
      }
    ]
  }
}

// --- The budget of this resource group ---

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: '${namePrefix}-monthly-${monthlyBudget}'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStart }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: alertEmails
        contactGroups: [alerts.id]
      }
      actual100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: alertEmails
        contactGroups: [cutoffGroup.id]
      }
    }
  }
}

// --- Alerts that say "look at this" ---

resource burst 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-request-burst'
  location: 'global'
  properties: {
    description: 'More than ${burstRequestsPer5Min} requests in 5 minutes: a scraper, a bot or a flood. One visitor cannot do that.'
    severity: 2
    enabled: true
    scopes: [app.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: app.location
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'requests'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.App/containerApps'
          metricName: 'Requests'
          operator: 'GreaterThan'
          threshold: burstRequestsPer5Min
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [{ actionGroupId: alerts.id }]
  }
}

resource awake 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-awake-6h'
  location: 'global'
  properties: {
    description: 'The replica has not scaled to zero for 6 hours: something keeps it awake, and awake time is what costs.'
    severity: 3
    enabled: true
    scopes: [app.id]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT6H'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: app.location
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'replicas'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.App/containerApps'
          metricName: 'Replicas'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          timeAggregation: 'Minimum'
        }
      ]
    }
    actions: [{ actionGroupId: alerts.id }]
  }
}

output cutoffPrincipalId string = cutoff.identity.principalId
output budgetName string = budget.name

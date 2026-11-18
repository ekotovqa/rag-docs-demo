// The demo in Azure Container Apps, after cycling-coach: one resource group, nothing running while nobody asks
// (minReplicas 0). The database is Neon Free, outside Azure, reached by its connection string from Key Vault.
// The image is in GHCR. Two stages: first everything but the app (deployApps=false); then, once Key Vault holds
// the two secrets (infra/secrets-to-keyvault.ps1) and GHCR the image, the app (deployApps=true, image=...).
targetScope = 'resourceGroup'

@description('Prefix of the resource names, lower case.')
param namePrefix string = 'ragdemo'

@description('Region of the vault, the identity and the logs.')
param location string = 'germanywestcentral'

@description('Region of the Container Apps environment and the app: Frankfurt refused new environments in Oct 2026, so it may differ.')
param appsLocation string = location

@description('Object id of the owner: may read and write the secrets in Key Vault.')
param adminObjectId string

@description('Second stage: the app. Needs the secrets in Key Vault and the image in GHCR.')
param deployApps bool = false

@description('The whole image: ghcr.io/<owner>/rag-docs-demo:<sha>.')
param image string = ''

@description('Subject of the token the deployment workflow presents: repo:<owner>@<id>/<repo>@<id>:ref:refs/heads/master.')
param deploySubject string

@description('Spend ceiling of the public demo per day, in USD; the app refuses questions above it.')
param dailyBudgetUsd string = '2'

@description('Questions per minute one visitor may ask.')
param rateLimitPerMinute string = '5'

@description('Questions per day one visitor (client IP) may ask: a few to see how the demo works.')
param visitorDailyQuestions string = '5'

@description('Spend ceiling of the public demo per month, in USD; the app refuses questions above it.')
param monthlyBudgetUsd string = '15'

var uniqueSuffix = uniqueString(resourceGroup().id)
var kvName = '${namePrefix}-kv-${take(uniqueSuffix, 24 - length(namePrefix) - 4)}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-identity'
  location: location
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource env 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${namePrefix}-env'
  location: appsLocation
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// --- Roles ---

var roleKvSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKvSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource identityKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, roleKvSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource adminKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, adminObjectId, roleKvSecretsOfficer)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsOfficer)
    principalId: adminObjectId
    principalType: 'User'
  }
}

// --- The app (second stage) ---

resource app 'Microsoft.App/containerApps@2025-01-01' = if (deployApps) {
  name: '${namePrefix}-app'
  location: appsLocation
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [identityKvRole]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'anthropic-api-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/anthropic-api-key'
          identity: identity.id
        }
        {
          name: 'database-url'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/database-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: image
          // The embedding model and the OCR engine live in the process; the first start indexes the corpus.
          resources: { cpu: json('1'), memory: '2Gi' }
          env: [
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'RAGDEMO_AUTO_INGEST', value: 'true' }
            { name: 'RAGDEMO_DAILY_BUDGET_USD', value: dailyBudgetUsd }
            { name: 'RAGDEMO_MONTHLY_BUDGET_USD', value: monthlyBudgetUsd }
            { name: 'RAGDEMO_RATE_LIMIT_PER_MINUTE', value: rateLimitPerMinute }
            { name: 'RAGDEMO_VISITOR_DAILY_QUESTIONS', value: visitorDailyQuestions }
            { name: 'PORT', value: '8000' }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/api/health', port: 8000 }
              periodSeconds: 5
              failureThreshold: 60
            }
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 30
              periodSeconds: 60
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// --- The deployment workflow's identity (GitHub Actions, OIDC) ---

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-deploy'
  location: location
}

resource deployTrust 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-master'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: deploySubject
    audiences: ['api://AzureADTokenExchange']
  }
}

var roleContainerAppsContributor = '358470bc-b998-42bd-ab17-a7e34c199c0f'
var roleIdentityOperator = 'f1a07417-d97a-45cb-824c-7a7467783830'

resource deployAppRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApps) {
  name: guid(resourceId('Microsoft.App/containerApps', '${namePrefix}-app'), deployIdentity.id, roleContainerAppsContributor)
  scope: app
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleContainerAppsContributor)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployEnvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(env.id, deployIdentity.id, roleContainerAppsContributor)
  scope: env
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleContainerAppsContributor)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployIdentityRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(identity.id, deployIdentity.id, roleIdentityOperator)
  scope: identity
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdentityOperator)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output keyVaultName string = keyVault.name
output environmentDomain string = env.properties.defaultDomain
output appUrl string = deployApps ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
output deployClientId string = deployIdentity.properties.clientId

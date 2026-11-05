<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <!-- Success banner (shown after a submitted order) -->
    <div v-if="lastOrder" class="success-banner">
      <div class="success-icon">&#10003;</div>
      <div class="success-text">
        <div class="success-title">{{ t('restocking.successTitle') }}</div>
        <div class="success-msg">
          {{ t('restocking.successMessage', { order: lastOrder.order_number, warehouse: translateWarehouse(lastOrder.warehouse) }) }}
        </div>
      </div>
      <div class="success-actions">
        <router-link to="/orders" class="btn-link">{{ t('restocking.viewOrders') }}</router-link>
        <button class="btn-ghost" @click="lastOrder = null">{{ t('restocking.close') }}</button>
      </div>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <!-- Budget + warehouse controls -->
      <div class="card controls-card">
        <div class="controls">
          <div class="control budget-control">
            <div class="control-label-row">
              <label for="budget">{{ t('restocking.budget') }}</label>
              <span class="budget-value">{{ formatCurrency(budget) }}</span>
            </div>
            <input
              id="budget"
              type="range"
              min="0"
              :max="budgetMax"
              :step="budgetStep"
              v-model.number="budget"
              class="budget-slider"
            />
            <div class="budget-ticks">
              <span>{{ formatCurrency(0) }}</span>
              <span>{{ formatCurrency(budgetMax) }}</span>
            </div>
          </div>

          <div class="control warehouse-control">
            <label for="warehouse">{{ t('restocking.warehouse') }}</label>
            <select id="warehouse" v-model="warehouse" class="warehouse-select">
              <option v-for="wh in warehouses" :key="wh" :value="wh">{{ translateWarehouse(wh) }}</option>
            </select>
            <div class="lead-time-hint">
              {{ t('orders.leadTime') }}: ~{{ leadTimeForSelected }} {{ t('restocking.days') }}
            </div>
          </div>
        </div>
      </div>

      <!-- Recommended items -->
      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">{{ t('restocking.recommendedItems') }}</h3>
            <p class="card-subtitle">{{ t('restocking.recommendedDescription') }}</p>
          </div>
        </div>

        <div v-if="items.length === 0" class="empty">{{ t('restocking.noItems') }}</div>
        <div v-else class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th class="col-select">{{ t('restocking.select') }}</th>
                <th class="col-sku">{{ t('restocking.sku') }}</th>
                <th class="col-name">{{ t('restocking.itemName') }}</th>
                <th class="col-trend">{{ t('restocking.trend') }}</th>
                <th class="col-gap">{{ t('restocking.forecastGap') }}</th>
                <th class="col-qty">{{ t('restocking.recommendedQty') }}</th>
                <th class="col-cost">{{ t('restocking.unitCost') }}</th>
                <th class="col-subtotal">{{ t('restocking.subtotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in items"
                :key="item.item_sku"
                :class="{ 'row-recommended': recommendedSet.has(item.item_sku), 'row-disabled': item.recommended_qty === 0 }"
              >
                <td class="col-select">
                  <input
                    type="checkbox"
                    :checked="selected.has(item.item_sku)"
                    :disabled="item.recommended_qty === 0"
                    @change="toggleItem(item.item_sku)"
                  />
                </td>
                <td class="col-sku"><strong>{{ item.item_sku }}</strong></td>
                <td class="col-name">
                  {{ item.item_name }}
                  <span v-if="recommendedSet.has(item.item_sku)" class="badge info">{{ t('restocking.recommended') }}</span>
                </td>
                <td class="col-trend"><span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span></td>
                <td class="col-gap">{{ item.forecast_gap }}</td>
                <td class="col-qty">{{ item.recommended_qty || '—' }}</td>
                <td class="col-cost">{{ formatCurrency(item.unit_cost) }}</td>
                <td class="col-subtotal">{{ item.recommended_qty > 0 ? formatCurrency(item.recommended_qty * item.unit_cost) : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Order summary / place order -->
        <div class="order-summary">
          <div class="summary-left">
            <div class="summary-line">
              <span>{{ t('restocking.selectedItems', { count: selectedItems.length }) }}</span>
              <span>{{ t('restocking.totalCost') }}: <strong>{{ formatCurrency(totalCost) }}</strong></span>
            </div>
            <div class="summary-line" :class="{ 'over-budget': overBudget }">
              <span>{{ t('restocking.remainingBudget') }}</span>
              <span>{{ overBudget ? t('restocking.overBudget') + ': ' : '' }}{{ formatCurrency(remainingBudget) }}</span>
            </div>
            <div v-if="selectedItems.length === 0" class="summary-note">{{ t('restocking.nothingSelected') }}</div>
          </div>
          <button
            class="btn-primary"
            :disabled="selectedItems.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, watch, computed } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

// Trend rank for budget-driven recommendation: items with rising demand are
// restocked first, stable items next, and decreasing items are never
// recommended (their recommended quantity is 0).
const TREND_RANK = { increasing: 0, stable: 1, decreasing: 2 }

export default {
  name: 'Restocking',
  setup() {
    const { t, translateWarehouse, currentCurrency } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const leadTimes = ref({})
    const lastOrder = ref(null)
    const submitting = ref(false)

    const budget = ref(5000)
    const budgetMax = 8000
    const budgetStep = 250
    const warehouse = ref('San Francisco')

    // Selected SKUs. A reactive Set so checkbox state tracks the recommendation.
    const selected = ref(new Set())

    const warehouses = computed(() => Object.keys(leadTimes.value))

    const leadTimeForSelected = computed(() => {
      return leadTimes.value[warehouse.value] ?? 10
    })

    // Derive per-item recommendation from the raw forecasts.
    // recommended_qty is the forecast gap (forecast - current), floored at 0 so
    // decreasing/stable items never over-recommend.
    const items = computed(() => {
      return forecasts.value
        .map(f => {
          const gap = f.forecasted_demand - f.current_demand
          return {
            ...f,
            forecast_gap: gap,
            recommended_qty: Math.max(0, gap)
          }
        })
        .sort((a, b) => (TREND_RANK[a.trend] - TREND_RANK[b.trend]) || (b.recommended_qty - a.recommended_qty))
    })

    // The budget-driven recommendation: walk items in priority order and greedily
    // include each item's full recommended quantity while the running cost stays
    // within budget. This set is what we pre-select when the budget changes.
    const recommendedSet = computed(() => {
      const set = new Set()
      let spent = 0
      for (const item of items.value) {
        if (item.recommended_qty === 0) continue
        const cost = item.recommended_qty * item.unit_cost
        if (spent + cost <= budget.value) {
          spent += cost
          set.add(item.item_sku)
        }
      }
      return set
    })

    const selectedItems = computed(() => {
      return items.value.filter(item => selected.value.has(item.item_sku) && item.recommended_qty > 0)
    })

    const totalCost = computed(() => {
      return selectedItems.value.reduce((sum, item) => sum + item.recommended_qty * item.unit_cost, 0)
    })

    const remainingBudget = computed(() => budget.value - totalCost.value)
    const overBudget = computed(() => remainingBudget.value < 0)

    // When the budget changes, re-sync the selection to the fresh recommendation.
    watch(budget, () => {
      selected.value = new Set(recommendedSet.value)
    })

    const toggleItem = (sku) => {
      if (selected.value.has(sku)) {
        selected.value.delete(sku)
      } else {
        selected.value.add(sku)
      }
      // Trigger reactivity (Set mutation) by reassigning.
      selected.value = new Set(selected.value)
    }

    const load = async () => {
      try {
        loading.value = true
        const [forecastData, leadTimeData] = await Promise.all([
          api.getDemandForecasts(),
          api.getRestockingLeadTimes()
        ])
        forecasts.value = forecastData
        leadTimes.value = leadTimeData

        if (!warehouses.value.includes(warehouse.value)) {
          warehouse.value = warehouses.value[0] || 'San Francisco'
        }
        selected.value = new Set(recommendedSet.value)
      } catch (err) {
        error.value = 'Failed to load restocking data: ' + err.message
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (selectedItems.value.length === 0) return
      submitting.value = true
      try {
        const payload = {
          warehouse: warehouse.value,
          items: selectedItems.value.map(item => ({
            sku: item.item_sku,
            name: item.item_name,
            quantity: item.recommended_qty,
            unit_cost: item.unit_cost
          }))
        }
        const created = await api.submitRestockOrder(payload)
        lastOrder.value = created
      } catch (err) {
        error.value = t('restocking.error') + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(load)

    return {
      t,
      translateWarehouse,
      formatCurrency,
      loading,
      error,
      items,
      recommendedSet,
      selected,
      toggleItem,
      selectedItems,
      totalCost,
      remainingBudget,
      overBudget,
      budget,
      budgetMax,
      budgetStep,
      warehouse,
      warehouses,
      leadTimeForSelected,
      lastOrder,
      submitting,
      placeOrder
    }
  }
}
</script>

<style scoped>
.controls-card {
  margin-bottom: 1.25rem;
}

.controls {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 2rem;
}

.control-label-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.control label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
  cursor: pointer;
}

.budget-ticks {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #94a3b8;
  margin-top: 0.25rem;
}

.warehouse-select {
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.938rem;
  color: #334155;
  background: white;
  margin-bottom: 0.5rem;
}

.lead-time-hint {
  font-size: 0.813rem;
  color: #64748b;
}

.card-subtitle {
  font-size: 0.875rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.empty {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}

.restock-table {
  table-layout: auto;
}

.col-select {
  width: 60px;
  text-align: center;
}

.restock-table .col-select input {
  width: 1.1rem;
  height: 1.1rem;
  accent-color: #2563eb;
  cursor: pointer;
}

.row-recommended {
  background: #eff6ff;
}

.row-disabled {
  opacity: 0.55;
}

.row-disabled input {
  cursor: not-allowed;
}

.order-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid #e2e8f0;
  flex-wrap: wrap;
}

.summary-left {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.summary-line {
  display: flex;
  gap: 1.5rem;
  font-size: 0.938rem;
  color: #334155;
}

.summary-line.over-budget {
  color: #dc2626;
  font-weight: 600;
}

.summary-note {
  font-size: 0.813rem;
  color: #94a3b8;
  font-style: italic;
}

.btn-primary {
  background: #0f172a;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 0.75rem 1.75rem;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1e293b;
}

.btn-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.success-banner {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  border-radius: 10px;
  padding: 1rem 1.25rem;
  margin-bottom: 1.25rem;
}

.success-icon {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #059669;
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.125rem;
  font-weight: 700;
  flex-shrink: 0;
}

.success-text {
  flex: 1;
}

.success-title {
  font-weight: 700;
  color: #065f46;
  font-size: 0.938rem;
}

.success-msg {
  font-size: 0.875rem;
  color: #047857;
  margin-top: 0.125rem;
}

.success-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.btn-link {
  color: #059669;
  font-weight: 600;
  font-size: 0.875rem;
  text-decoration: none;
}

.btn-link:hover {
  text-decoration: underline;
}

.btn-ghost {
  background: transparent;
  border: 1px solid #a7f3d0;
  color: #047857;
  border-radius: 6px;
  padding: 0.375rem 0.75rem;
  font-size: 0.813rem;
  font-weight: 600;
  cursor: pointer;
}

@media (max-width: 720px) {
  .controls {
    grid-template-columns: 1fr;
  }
}
</style>
